"""P0：工作流生命周期字段由 Runtime CAS 统一提交的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.errors import LeaseError, RuntimeValidationError, StateConflictError
from runtime.execution import ExecutionContext, LocalCompatibilityEnvironment
from runtime.control_plane import initialize_control_plane
from runtime.leases import LeaseManager
from runtime.orchestrator import Orchestrator
from runtime.project_revision import ProjectStateCAS
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state
from tests.test_project_migration import v4_state


def _bootstrap(tmp_path: Path, status: str = "INTAKE") -> dict[str, object]:
    """创建隔离的 test_ managed project 与 F10 Session。"""

    root = tmp_path / "test_todo_app"
    root.mkdir()
    control_home = tmp_path / "control-plane"
    control_plane = initialize_control_plane("test_todo_app", home=control_home)
    store = SessionStore(control_plane / "sessions.sqlite3")
    session = store.create_session(
        "test_todo_app", root, idempotency_key=f"p0-session-{status}"
    )
    state = v4_state(status)
    state["project_id"] = "test_todo_app"
    state = preview_runtime_migration(
        state, project_root=root, session_id=session.session_id
    )
    project = root / "project.yaml"
    project.write_text(serialize_project_state(state), encoding="utf-8")
    leases = LeaseManager(store)
    lease = leases.acquire(session.session_id, "worker-p0")
    return {
        "root": root,
        "control_home": control_home,
        "project": project,
        "state": state,
        "store": store,
        "leases": leases,
        "cas": ProjectStateCAS(store, leases),
        "session_id": session.session_id,
        "lease": lease,
    }


def _commit_patch(
    fixture: dict[str, object],
    changed_fields: dict[str, object],
    *,
    source_status: str,
    target_status: str,
    actor_role: str,
    expected_revision: int = 0,
) -> dict[str, object]:
    lease = fixture["lease"]
    return fixture["cas"].commit_patch(  # type: ignore[union-attr]
        fixture["project"],  # type: ignore[arg-type]
        changed_fields,
        source_status=source_status,
        target_status=target_status,
        session_id=fixture["session_id"],  # type: ignore[arg-type]
        worker_id="worker-p0",
        actor_role=actor_role,
        lease_version=lease.lease_version,  # type: ignore[union-attr]
        lease_token=lease.lease_token or "",  # type: ignore[union-attr]
        expected_revision=expected_revision,
        idempotency_key=f"p0-{source_status}-{target_status}-{actor_role}",
    )


def _prepare_intake_fixture(fixture: dict[str, object]) -> None:
    root = fixture["root"]
    for reference in (
        "memory/requirements/requirements_v001.yaml",
        "memory/requirements/interview-001.md",
    ):
        path = root / reference  # type: ignore[operator]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test\n", encoding="utf-8")


def test_first_ask_to_planner_lifecycle_cas_passes(tmp_path: Path) -> None:
    fixture = _bootstrap(tmp_path, "INTAKE")
    _prepare_intake_fixture(fixture)

    result = _commit_patch(
        fixture,
        {
            "status": "PLANNING",
            "next_role": "planner",
            "active_module": None,
            "requirements_status": "sufficient_for_planning",
            "requirements_version": 1,
            "active_requirements": "memory/requirements/requirements_v001.yaml",
            "active_interview": "memory/requirements/interview-001.md",
            "intake_round": 1,
        },
        source_status="INTAKE",
        target_status="PLANNING",
        actor_role="first_ask_intake",
    )

    state = load_project_state(fixture["project"])  # type: ignore[arg-type]
    assert result["result"] == "COMMITTED"
    assert state["status"] == "PLANNING"
    assert state["next_role"] == "planner"
    assert state["active_module"] is None
    assert state["runtime"]["revision"] == 1


def test_planner_lifecycle_commit_reaches_product_approval_gate(tmp_path: Path) -> None:
    fixture = _bootstrap(tmp_path, "PLANNING")
    root = fixture["root"]
    state = fixture["state"]
    state.update(
        {
            "next_role": "planner",
            "requirements_status": "sufficient_for_planning",
            "requirements_version": 1,
            "active_requirements": "memory/requirements/requirements_v001.yaml",
            "design_exploration_required": False,
            "design_review_status": "skipped_by_user",
            "design_skip_record": "memory/decisions/design-skip-001.md",
        }
    )
    for reference in (
        "memory/requirements/requirements_v001.yaml",
        "memory/decisions/design-skip-001.md",
        "memory/proposals/product_proposal_v001.md",
    ):
        path = root / reference  # type: ignore[operator]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test\n", encoding="utf-8")
    fixture["project"].write_text(  # type: ignore[union-attr]
        serialize_project_state(state), encoding="utf-8"
    )

    _commit_patch(
        fixture,
        {
            "status": "WAITING_FOR_PRODUCT_REVIEW",
            "next_role": "planner",
            "active_module": None,
            "proposal_status": "waiting_user_review",
            "proposal_version": 1,
            "active_proposal": "memory/proposals/product_proposal_v001.md",
        },
        source_status="PLANNING",
        target_status="WAITING_FOR_PRODUCT_REVIEW",
        actor_role="planner",
    )

    state = load_project_state(fixture["project"])  # type: ignore[arg-type]
    assert state["status"] == "WAITING_FOR_PRODUCT_REVIEW"
    assert state["proposal_status"] == "waiting_user_review"
    assert state["runtime"]["revision"] == 1


def test_minimal_e2e_uses_orchestrator_first_ask_and_planner(tmp_path: Path) -> None:
    fixture = _bootstrap(tmp_path, "INTAKE")
    _prepare_intake_fixture(fixture)
    orchestrator = Orchestrator(
        fixture["root"],  # type: ignore[arg-type]
        control_plane_home=fixture["control_home"],  # type: ignore[arg-type]
    )

    # 测试夹具明确记录用户同意跳过设计探索，才能进入产品方案审核 Gate。
    for reference in (
        "memory/decisions/design-skip-001.md",
        "memory/proposals/product_proposal_v001.md",
    ):
        path = fixture["root"] / reference  # type: ignore[operator]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test\n", encoding="utf-8")

    bootstrap_lease = fixture["lease"]
    fixture["leases"].release(  # type: ignore[union-attr]
        fixture["session_id"],
        "worker-p0",
        bootstrap_lease.lease_version,  # type: ignore[union-attr]
        bootstrap_lease.lease_token or "",  # type: ignore[union-attr]
    )
    intake = orchestrator.start(worker_id="worker-p0")
    assert intake["selection"].kind == "MODULE"
    assert intake["selection"].target == "first_ask_intake"
    orchestrator.cas.commit_patch(
        fixture["project"],  # type: ignore[arg-type]
        {
            "status": "PLANNING",
            "next_role": "planner",
            "active_module": None,
            "requirements_status": "sufficient_for_planning",
            "requirements_version": 1,
            "active_requirements": "memory/requirements/requirements_v001.yaml",
            "active_interview": "memory/requirements/interview-001.md",
            "intake_round": 1,
        },
        source_status="INTAKE",
        target_status="PLANNING",
        session_id=intake["session_id"],
        worker_id="worker-p0",
        actor_role="first_ask_intake",
        lease_version=intake["lease_version"],
        lease_token=intake["lease_token"],
        expected_revision=0,
        idempotency_key="p0-e2e-first-ask",
    )
    orchestrator.leases.release(
        intake["session_id"],  # type: ignore[arg-type]
        "worker-p0",
        intake["lease_version"],  # type: ignore[arg-type]
        intake["lease_token"],  # type: ignore[arg-type]
    )

    planner = orchestrator.start(worker_id="worker-p0")
    assert planner["selection"].kind == "ROLE"
    assert planner["selection"].target == "planner"
    assert planner["run_id"] is not None
    orchestrator.commit_step(
        planner["session_id"],  # type: ignore[arg-type]
        planner["run_id"],  # type: ignore[arg-type]
        planner["lease_token"],  # type: ignore[arg-type]
        {
            "source_status": "PLANNING",
            "target_status": "WAITING_FOR_PRODUCT_REVIEW",
            "changed_fields": {
                "status": "WAITING_FOR_PRODUCT_REVIEW",
                "next_role": "planner",
                "active_module": None,
                "design_exploration_required": False,
                "design_review_status": "skipped_by_user",
                "design_skip_record": "memory/decisions/design-skip-001.md",
                "proposal_status": "waiting_user_review",
                "proposal_version": 1,
                "active_proposal": "memory/proposals/product_proposal_v001.md",
            },
            "expected_revision": 1,
            "idempotency_key": "p0-e2e-planner-proposal",
        },
    )

    state = load_project_state(fixture["project"])  # type: ignore[arg-type]
    assert state["status"] == "WAITING_FOR_PRODUCT_REVIEW"
    assert state["proposal_status"] == "waiting_user_review"
    assert state["runtime"]["revision"] == 2


def test_illegal_transition_is_denied(tmp_path: Path) -> None:
    fixture = _bootstrap(tmp_path, "PLANNING")
    with pytest.raises(RuntimeValidationError, match="ILLEGAL_STATE_TRANSITION"):
        _commit_patch(
            fixture,
            {"status": "ACCEPTED", "next_role": None, "active_module": None},
            source_status="PLANNING",
            target_status="ACCEPTED",
            actor_role="planner",
        )


@pytest.mark.parametrize(
    ("actor", "field"),
    (("planner", "last_generator_response"), ("generator", "last_evaluation"), ("evaluator", "active_plan")),
)
def test_role_owned_field_boundaries_remain_denied(
    tmp_path: Path, actor: str, field: str
) -> None:
    fixture = _bootstrap(tmp_path, "PLANNING")
    candidate = dict(fixture["state"])  # type: ignore[arg-type]
    candidate[field] = "unauthorized"
    lease = fixture["lease"]
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        fixture["cas"].commit(  # type: ignore[union-attr]
            fixture["project"],  # type: ignore[arg-type]
            candidate,
            session_id=fixture["session_id"],  # type: ignore[arg-type]
            worker_id="worker-p0",
            actor_role=actor,
            lease_version=lease.lease_version,  # type: ignore[union-attr]
            lease_token=lease.lease_token or "",  # type: ignore[union-attr]
            expected_revision=0,
            idempotency_key=f"p0-illegal-{actor}-{field}",
        )


def test_role_cannot_directly_submit_lifecycle_fields(tmp_path: Path) -> None:
    fixture = _bootstrap(tmp_path, "PLANNING")
    candidate = dict(fixture["state"])  # type: ignore[arg-type]
    candidate.update({"status": "WAITING_FOR_PRODUCT_REVIEW", "next_role": "planner"})
    lease = fixture["lease"]
    with pytest.raises(RuntimeValidationError, match="ROLE_FIELD_OWNERSHIP_VIOLATION"):
        fixture["cas"].commit(  # type: ignore[union-attr]
            fixture["project"],  # type: ignore[arg-type]
            candidate,
            session_id=fixture["session_id"],  # type: ignore[arg-type]
            worker_id="worker-p0",
            actor_role="planner",
            lease_version=lease.lease_version,  # type: ignore[union-attr]
            lease_token=lease.lease_token or "",  # type: ignore[union-attr]
            expected_revision=0,
            idempotency_key="p0-direct-lifecycle",
        )


def test_stale_revision_and_lease_are_denied(tmp_path: Path) -> None:
    fixture = _bootstrap(tmp_path, "PLANNING")
    lease = fixture["lease"]
    with pytest.raises(StateConflictError):
        _commit_patch(
            fixture,
            {},
            source_status="PLANNING",
            target_status="PLANNING",
            actor_role="planner",
            expected_revision=1,
        )

    fixture["leases"].revoke_for_lifecycle(  # type: ignore[union-attr]
        fixture["session_id"], reason="p0-stale-lease"
    )
    with pytest.raises(LeaseError):
        fixture["cas"].commit_patch(  # type: ignore[union-attr]
            fixture["project"],  # type: ignore[arg-type]
            {},
            source_status="PLANNING",
            target_status="PLANNING",
            session_id=fixture["session_id"],  # type: ignore[arg-type]
            worker_id="worker-p0",
            actor_role="planner",
            lease_version=lease.lease_version,
            lease_token=lease.lease_token or "",
            expected_revision=0,
            idempotency_key="p0-stale-lease",
        )


def test_local_backend_cannot_write_project_yaml(tmp_path: Path) -> None:
    root = tmp_path / "test_todo_app"
    root.mkdir()
    (root / "code").mkdir()
    context = ExecutionContext(
        session_id="p0-session",
        run_id="p0-run",
        worker_id="worker-p0",
        lease_version=1,
        role="generator",
        project_id="test_todo_app",
        project_root=str(root),
    )
    environment = LocalCompatibilityEnvironment()
    environment.provision(context)
    with pytest.raises(RuntimeValidationError, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
        environment.write_file(context, "project.yaml", "status: ACCEPTED\n")
