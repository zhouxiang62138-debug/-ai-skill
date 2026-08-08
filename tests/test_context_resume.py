"""F13.3 durable Context Manifest、Delta 与 Resume 测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime.context import (
    ContextBuildRequest,
    ContextBuilder,
    ContextPolicy,
    resume_context,
)
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.event_types import EventType
from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.project_state import load_project_state, serialize_project_state
from tests.test_context_budget import _write_policy
from tests.test_formal_context_builder import _prepare_project


def _request(session_id: str, run_id: str, *references: str) -> ContextBuildRequest:
    return ContextBuildRequest(session_id, run_id, "generator", tuple(references))


def test_first_resume_is_full_then_unchanged_is_incremental(tmp_path: Path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    first = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    second = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    assert first.resume_mode == "FULL_BUILD"
    assert second.resume_mode == "INCREMENTAL"
    assert second.previous_context_id == first.context_id
    assert second.unchanged_sources
    assert not second.added_sources


def test_added_modified_removed_and_hash_not_mtime(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    first = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    (root / "code/extra.py").write_text("extra\n", encoding="utf-8")
    added = builder.build_resume(
        _request(session_id, run_id, "code/main.py", "code/extra.py")
    )
    assert any(item.reference == "code/extra.py" for item in added.added_sources)
    (root / "code/main.py").write_text("print('changed')\n", encoding="utf-8")
    modified = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    assert any(item.reference == "code/main.py" for item in modified.modified_sources)
    os.utime(root / "code/main.py")
    unchanged = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    assert any(item.reference == "code/main.py" for item in unchanged.unchanged_sources)
    removed = builder.build_resume(_request(session_id, run_id))
    assert any(item.reference == "code/main.py" for item in removed.removed_sources)
    assert first.context_hash != modified.context_hash


def test_durable_manifest_has_no_context_content(tmp_path: Path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    package = ContextBuilder(store).build(
        _request(session_id, run_id, "code/main.py")
    )
    durable = store.get_context_manifest(session_id, package.context_id)
    assert durable["context_hash"] == package.context_hash
    assert durable["sources"]
    assert all("content" not in source for source in durable["sources"])
    assert "print('ok')" not in str(durable)


def test_planner_manifest_is_not_reused_by_generator(tmp_path: Path) -> None:
    store, session_id, planner_run, root = _prepare_project(
        tmp_path, "PLANNING", "planner"
    )
    ContextBuilder(store).build_resume(
        ContextBuildRequest(session_id, planner_run, "planner")
    )
    state = load_project_state(root / "project.yaml")
    state["status"] = "IMPLEMENTING"
    state["next_role"] = "generator"
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    generator_run = store.create_role_run(session_id, "worker-next", "generator")
    result = ContextBuilder(store).build_resume(
        _request(session_id, generator_run)
    )
    assert result.resume_mode == "FULL_BUILD"
    assert result.previous_context_id is None


def test_project_and_session_scope_prevent_reuse(tmp_path: Path) -> None:
    store_a, session_a, run_a, _ = _prepare_project(
        tmp_path / "a", "IMPLEMENTING", "generator"
    )
    ContextBuilder(store_a).build_resume(_request(session_a, run_a))
    store_b, session_b, run_b, _ = _prepare_project(
        tmp_path / "b", "IMPLEMENTING", "generator"
    )
    result = ContextBuilder(store_b).build_resume(_request(session_b, run_b))
    assert result.resume_mode == "FULL_BUILD"
    assert result.previous_context_id is None
    assert store_b.find_previous_context_manifest(
        session_b, project_id="test_migration", role="generator"
    ) is not None
    assert store_a.find_previous_context_manifest(
        session_a, project_id="test_migration", role="generator"
    ) is not None


def test_different_session_has_no_previous_manifest(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    ContextBuilder(store).build_resume(_request(session_id, run_id))
    other_session = store.create_session(
        "test_migration", root, idempotency_key="second-session"
    )
    assert store.find_previous_context_manifest(
        other_session.session_id,
        project_id="test_migration",
        role="generator",
    ) is None


def test_role_state_change_revalidates_before_resume(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    ContextBuilder(store).build_resume(_request(session_id, run_id))
    state = load_project_state(root / "project.yaml")
    state["status"] = "EVALUATING"
    state["next_role"] = "evaluator"
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_ROLE_STATE_MISMATCH"):
        ContextBuilder(store).build_resume(_request(session_id, run_id))


def test_path_policy_change_blocks_old_source_reuse(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    builder.build_resume(_request(session_id, run_id, "code/main.py"))
    restricted = root / "restricted-role-policy.yaml"
    restricted.write_text(
        """roles:
  planner:
    reads:
      - project.yaml
    writes: []
    prohibited: []
    write_prohibited: []
  generator:
    reads:
      - project.yaml
      - memory/plans/
      - memory/specifications/
      - evaluation/issues/
      - memory/handoffs/
    writes: []
    prohibited: []
    write_prohibited: []
  evaluator:
    reads:
      - project.yaml
    writes: []
    prohibited: []
    write_prohibited: []
""",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_NOT_ALLOWED"):
        ContextBuilder(
            store,
            path_policy=ExecutionPathPolicy(restricted),
        ).build_resume(_request(session_id, run_id, "code/main.py"))


def test_context_policy_change_forces_full_build(tmp_path: Path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    ContextBuilder(store).build_resume(_request(session_id, run_id))
    changed_policy = _write_policy(tmp_path / "changed-context.yaml")
    result = ContextBuilder(
        store, context_policy=ContextPolicy(changed_policy)
    ).build_resume(_request(session_id, run_id))
    assert result.resume_mode == "FULL_BUILD"
    assert result.previous_context_id is None


def test_revision_rollback_is_rejected(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    builder.build_resume(_request(session_id, run_id))
    state = load_project_state(root / "project.yaml")
    state["runtime"]["revision"] = 1
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    builder.build(_request(session_id, run_id))
    state["runtime"]["revision"] = 0
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_RESUME_REVISION_MISMATCH"):
        builder.build_resume(_request(session_id, run_id))


def test_incomplete_or_corrupt_previous_manifest_falls_back_to_full_build(
    tmp_path: Path,
) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    first = builder.build_resume(_request(session_id, run_id))
    connection = store.raw_connection()
    connection.execute(
        "UPDATE context_manifests SET complete=0 WHERE context_id=?",
        (first.context_id,),
    )
    connection.commit()
    connection.close()
    result = builder.build_resume(_request(session_id, run_id))
    assert result.resume_mode == "FULL_BUILD"
    connection = store.raw_connection()
    connection.execute(
        "UPDATE context_manifests SET sources_json=? WHERE context_id=?",
        ("{broken", result.context_id),
    )
    connection.commit()
    connection.close()
    recovered = builder.build_resume(_request(session_id, run_id))
    assert recovered.resume_mode == "FULL_BUILD"


def test_crash_and_pause_resume_use_durable_manifest(tmp_path: Path) -> None:
    store, session_id, first_run, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    builder.build(_request(session_id, first_run, "code/main.py"))
    next_run = store.create_role_run(session_id, "worker-recovered", "generator")
    store.set_session_status(session_id, "PAUSED")
    result = resume_context(
        store, session_id, next_run, "generator", additional_references=("code/main.py",)
    )
    assert result.resume_mode == "INCREMENTAL"
    assert result.base_revision == result.current_revision


def test_incremental_final_hash_equals_equivalent_full_build(tmp_path: Path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    first = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    incremental = builder.build_resume(_request(session_id, run_id, "code/main.py"))
    full = builder.build(_request(session_id, run_id, "code/main.py"))
    assert first.resume_mode == "FULL_BUILD"
    assert incremental.resume_mode == "INCREMENTAL"
    assert incremental.context_hash == full.context_hash


def test_delta_order_and_resume_audit_are_deterministic_and_safe(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    (root / "code/aaa.py").write_text("a\n", encoding="utf-8")
    (root / "code/zzz.py").write_text("z\n", encoding="utf-8")
    builder = ContextBuilder(store)
    builder.build_resume(_request(session_id, run_id))
    result = builder.build_resume(
        _request(session_id, run_id, "code/zzz.py", "code/aaa.py")
    )
    added_refs = [item.reference for item in result.added_sources]
    assert added_refs == sorted(added_refs)
    events = [
        event
        for event in store.list_events(session_id)
        if event.event_type == EventType.CONTEXT_RESUMED
    ]
    assert events
    assert "sources" not in str(events[-1].payload)
    assert "content" not in str(events[-1].payload)


def test_context_manifest_lookup_is_session_and_role_scoped(tmp_path: Path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    package = builder.build(_request(session_id, run_id))
    assert store.find_previous_context_manifest(
        session_id,
        project_id=package.project_id,
        role="planner",
    ) is None
    with pytest.raises(RuntimeStorageError, match="CONTEXT_MANIFEST_MISSING"):
        store.get_context_manifest(session_id, "context-missing")
