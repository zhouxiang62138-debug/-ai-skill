from pathlib import Path

import pytest

from runtime.control_plane import (
    control_plane_root,
    initialize_control_plane,
    require_session_database,
    session_database_path,
)
from runtime.errors import RuntimeStorageError
from runtime.orchestrator import Orchestrator
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import serialize_project_state
from scripts.project_state import load_project_state
from tests.test_project_migration import v4_state


def test_session_store_outside_project_root(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    database = session_database_path("demo", home=tmp_path / "home")

    assert not database.is_relative_to(project_root)
    assert database.parent == control_plane_root("demo", home=tmp_path / "home")


def test_v7_missing_session_store_is_blocked(tmp_path: Path) -> None:
    with pytest.raises(RuntimeStorageError, match="RUNTIME_HISTORY_MISSING"):
        require_session_database("missing", home=tmp_path)


def test_control_plane_initialization_is_explicit(tmp_path: Path) -> None:
    root = initialize_control_plane("demo", home=tmp_path)

    assert root.is_dir()
    assert (root / "tool-results").is_dir()
    assert (root / "checkpoints").is_dir()
    assert (root / "locks").is_dir()
    assert not (root / "sessions.sqlite3").exists()


def test_bound_v7_project_does_not_silently_recreate_session_store(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = preview_runtime_migration(v4_state(), project_root=project)
    (project / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")

    with pytest.raises(RuntimeStorageError, match="RUNTIME_HISTORY_MISSING"):
        Orchestrator(project, control_plane_home=tmp_path / "home")


def test_checkpoint_does_not_create_stale_yaml_pointer(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = preview_runtime_migration(v4_state(), project_root=project)
    (project / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")
    control = initialize_control_plane("test_migration", home=tmp_path / "home")
    store = SessionStore(control / "sessions.sqlite3")
    session = store.create_session("test_migration", project, idempotency_key="session", session_id=state["runtime"]["session_id"])
    store.append_event(session.session_id, "ROLE_SELECTED", "ORCHESTRATOR", "test", idempotency_key="event", correlation_id=session.session_id, payload={})
    store.create_checkpoint(session.session_id, project_revision=0, project_state_hash="hash", active_role=None, active_module=None, status="PLANNING", next_role="planner", open_transaction_ids=[])

    assert set(load_project_state(project / "project.yaml")["runtime"]) == {"session_id", "control_plane_id", "revision"}
