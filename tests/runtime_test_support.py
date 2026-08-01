"""F10 Runtime 测试夹具。"""

from __future__ import annotations

from pathlib import Path

from runtime.session_store import SessionStore
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
    store = SessionStore(root / ".runtime" / "sessions.sqlite3")
    state = v4_state(status)
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
    return root, session.session_id
