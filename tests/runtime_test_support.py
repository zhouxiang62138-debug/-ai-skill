"""F10 Runtime 测试夹具。"""

from __future__ import annotations

from pathlib import Path

from runtime.session_store import SessionStore
from runtime.attestation import required_steps_hash
from runtime.context import ContextBuildRequest, ContextBuilder
from scripts.project_state import load_project_state
from runtime.control_plane import initialize_control_plane, session_database_path
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import serialize_project_state
from tests.test_project_migration import v4_state


def make_store(directory: str | Path) -> tuple[SessionStore, str]:
    root = Path(directory)
    project = root / "project"
    project.mkdir()
    store = SessionStore(root / "sessions.sqlite3")
    session = store.create_session(
        "test_runtime", project, idempotency_key="test-session"
    )
    return store, session.session_id


def make_runtime_project(directory: str | Path, *, status: str = "PLANNING") -> tuple[Path, str]:
    root = Path(directory)
    root.mkdir(exist_ok=True)
    state = v4_state(status)
    control_home = root / "control-home"
    store = SessionStore(
        initialize_control_plane(state["project_id"], home=control_home)
        / "sessions.sqlite3"
    )
    if status == "PLANNING":
        state["next_role"] = "planner"
        state["active_module"] = None
        state["requirements_status"] = "sufficient_for_planning"
        state["active_requirements"] = "memory/requirements/requirements_v001.yaml"
        requirement = root / state["active_requirements"]
        requirement.parent.mkdir(parents=True, exist_ok=True)
        requirement.write_text("schema_version: 1\n", encoding="utf-8")
    session = store.create_session(
        state["project_id"], root, idempotency_key="runtime-project-session"
    )
    runtime_state = preview_runtime_migration(
        state, project_root=root, session_id=session.session_id
    )
    (root / "project.yaml").write_text(
        serialize_project_state(runtime_state), encoding="utf-8"
    )
    (root / ".test-control-plane-home").write_text(str(control_home), encoding="utf-8")
    return root, session.session_id


def open_runtime_store(project_root: str | Path) -> SessionStore:
    """按测试项目的外部绑定重开同一个控制平面。"""

    root = Path(project_root)
    control_home = Path((root / ".test-control-plane-home").read_text(encoding="utf-8"))
    state = preview_runtime_migration(v4_state(), project_root=root)
    return SessionStore(session_database_path(state["project_id"], home=control_home))


def commit_step_with_test_attestation(
    orchestrator: object,
    session_id: str,
    run_id: str,
    lease_token: str,
    result: dict[str, object],
) -> dict[str, object]:
    """测试夹具通过真实 Runtime 记录 Attestation 后调用严格提交入口。"""

    store = orchestrator.store  # type: ignore[attr-defined]
    role = str(store.get_role_run(session_id, run_id)["role"])
    context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, run_id, role)
    )
    invocation = store.create_model_invocation(
        session_id,
        run_id,
        role,
        context.context_id,
        idempotency_key=f"test-attestation-invocation:{result['idempotency_key']}",
    )
    state = load_project_state(orchestrator.root / "project.yaml")  # type: ignore[attr-defined]
    attestation = store.create_phase_attestation(
        session_id=session_id,
        run_id=run_id,
        role=role,
        project_revision=int(state["runtime"]["revision"]),
        context_id=context.context_id,
        invocation_id=str(invocation["invocation_id"]),
        required_steps_hash=required_steps_hash(("legacy_transition",)),
        verifier_results={
            "legacy_transition": {
                "passed": True,
                "evidence_refs": ["test:legacy-transition"],
                "details": "显式 test-only 夹具证明",
            }
        },
        idempotency_key=f"test-attestation:{result['idempotency_key']}",
    )
    prepared = dict(result)
    prepared["attestation_id"] = attestation["attestation_id"]
    return orchestrator.commit_step(session_id, run_id, lease_token, prepared)  # type: ignore[attr-defined]
