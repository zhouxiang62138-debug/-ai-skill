from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from runtime.leases import LeaseManager
from runtime.project_revision import ProjectStateCAS
from scripts.exploration import finalize_preview_round
from scripts.project_state import ProjectStateError, load_project_state
from tests.runtime_test_support import make_runtime_project, open_runtime_store
from tests.test_exploration import create_valid_round


def _runtime(tmp_path: Path):
    root, session_id = make_runtime_project(tmp_path)
    store = open_runtime_store(root)
    leases = LeaseManager(store)
    lease = leases.acquire(session_id, "planner-resilience-worker")
    return root, session_id, store, leases, lease, ProjectStateCAS(store, leases)


def _commit_kwargs(session_id: str, lease) -> dict[str, object]:
    return {
        "session_id": session_id,
        "worker_id": "planner-resilience-worker",
        "actor_role": "planner",
        "lease_version": lease.lease_version,
        "lease_token": lease.lease_token or "",
    }


def test_runtime_v3_copies_legacy_revisions_without_deleting_history(
    tmp_path: Path,
) -> None:
    """现有 v2 数据库升级后，旧表和 ABORTED 历史都必须原样保留。"""

    database = tmp_path / "legacy-runtime.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE runtime_schema(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE sessions(
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            project_root TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_sequence INTEGER NOT NULL DEFAULT 0,
            last_checkpoint_id TEXT,
            active_worker_id TEXT
        );
        CREATE TABLE state_revisions(
            revision_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            expected_revision INTEGER NOT NULL,
            new_revision INTEGER NOT NULL,
            before_hash TEXT NOT NULL,
            after_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            caused_by_event_id TEXT,
            created_at TEXT NOT NULL,
            committed_at TEXT,
            UNIQUE(session_id, new_revision),
            UNIQUE(session_id, idempotency_key)
        );
        INSERT INTO runtime_schema VALUES(2, '2026-08-11T00:00:00+00:00');
        INSERT INTO sessions VALUES(
            'session-111111111111111111111111', 'test_legacy', '.', 'ACTIVE',
            '2026-08-11T00:00:00+00:00', '2026-08-11T00:00:00+00:00', 0, NULL, NULL
        );
        INSERT INTO state_revisions VALUES(
            'revision-111111111111111111111111',
            'session-111111111111111111111111', 0, 1,
            'before', 'after', 'ABORTED', 'legacy-aborted', NULL,
            '2026-08-11T00:00:00+00:00', NULL
        );
        """
    )
    connection.commit()
    connection.close()

    from runtime.session_store import SessionStore

    store = SessionStore(database)
    migrated = store.get_state_revision_by_idempotency(
        "session-111111111111111111111111", "legacy-aborted"
    )
    assert migrated is not None
    assert migrated["status"] == "ABORTED"
    connection = store.raw_connection()
    try:
        legacy_count = connection.execute(
            "SELECT COUNT(*) FROM state_revisions WHERE idempotency_key='legacy-aborted'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert legacy_count == 1


def test_invalid_candidate_does_not_allocate_revision_attempt(tmp_path: Path) -> None:
    """可预见的字段错误必须在创建 PENDING revision 前失败。"""

    root, session_id, store, _, lease, cas = _runtime(tmp_path)
    state = load_project_state(root / "project.yaml")
    invalid = dict(state)
    invalid["active_proposal"] = "memory/proposals/missing.md"

    with pytest.raises(ProjectStateError, match="CAS 候选状态无效"):
        cas.commit(
            root / "project.yaml",
            invalid,
            expected_revision=0,
            idempotency_key="invalid-before-revision",
            **_commit_kwargs(session_id, lease),
        )

    connection = store.raw_connection()
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM state_revision_attempts WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_aborted_attempt_does_not_block_corrected_same_revision(tmp_path: Path) -> None:
    """崩溃尝试保留审计，但修正版无需删库即可提交同一业务 revision。"""

    root, session_id, store, _, lease, cas = _runtime(tmp_path)
    state = load_project_state(root / "project.yaml")
    with pytest.raises(OSError, match="提交前崩溃"):
        cas.commit(
            root / "project.yaml",
            state,
            expected_revision=0,
            idempotency_key="crash-attempt",
            fail_at="before_project_state_commit",
            **_commit_kwargs(session_id, lease),
        )

    assert cas.recover_pending(root / "project.yaml", session_id)
    corrected = dict(state)
    corrected["proposal_status"] = "draft"
    result = cas.commit(
        root / "project.yaml",
        corrected,
        expected_revision=0,
        idempotency_key="corrected-attempt",
        **_commit_kwargs(session_id, lease),
    )

    assert result["result"] == "COMMITTED"
    connection = store.raw_connection()
    try:
        rows = connection.execute(
            """
            SELECT status FROM state_revision_attempts
            WHERE session_id=? AND new_revision=1 ORDER BY created_at
            """,
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    assert sorted(row["status"] for row in rows) == ["ABORTED", "COMMITTED"]


def test_planner_design_flow_derives_lifecycle_and_wait_fields(tmp_path: Path) -> None:
    """复现本次对话：目录尚未创建也能进入生成态，完成态一次提交成功。"""

    root, session_id, _, _, lease, cas = _runtime(tmp_path)
    proposal_ref = "memory/proposals/product_proposal_v001.md"
    proposal = root / proposal_ref
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("# 产品方案\n", encoding="utf-8")
    round_ref = "artifacts/design_previews/round_001"

    first = cas.commit_patch(
        root / "project.yaml",
        {
            "proposal_status": "draft",
            "proposal_version": 1,
            "active_proposal": proposal_ref,
            "design_exploration_required": True,
            "exploration_trigger_reasons": ["visual_preferences_undecided"],
            "design_review_status": "generating",
            "design_preview_round": 1,
            "active_design_preview_round": round_ref,
            "exploration_generation_attempt": 1,
        },
        source_status="PLANNING",
        target_status="DESIGN_EXPLORATION",
        expected_revision=0,
        idempotency_key="begin-design-without-directory",
        **_commit_kwargs(session_id, lease),
    )
    assert first["result"] == "COMMITTED"
    generating = load_project_state(root / "project.yaml")
    assert generating["status"] == "DESIGN_EXPLORATION"
    assert generating["next_role"] == "planner"
    assert generating["active_module"] is None
    assert not (root / round_ref).exists()

    create_valid_round(root)
    waiting_candidate = finalize_preview_round(generating, root)
    lifecycle = {"status", "next_role", "active_module"}
    business_patch = {
        key: value
        for key, value in waiting_candidate.items()
        if key not in lifecycle and generating.get(key) != value
    }
    second = cas.commit_patch(
        root / "project.yaml",
        business_patch,
        source_status="DESIGN_EXPLORATION",
        target_status="WAITING_FOR_DESIGN_REVIEW",
        expected_revision=1,
        idempotency_key="finish-design-with-complete-wait-state",
        **_commit_kwargs(session_id, lease),
    )

    assert second["result"] == "COMMITTED"
    waiting = load_project_state(root / "project.yaml")
    assert waiting["status"] == "WAITING_FOR_DESIGN_REVIEW"
    assert waiting["design_review_status"] == "waiting_user_selection"
    assert waiting["design_feedback_status"] == "waiting_user_feedback"
    assert waiting["next_role"] == "planner"
