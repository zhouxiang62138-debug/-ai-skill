"""F10 Runtime 测试夹具。"""

from __future__ import annotations

from pathlib import Path

from runtime.session_store import SessionStore
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
