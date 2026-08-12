from pathlib import Path

from runtime.control_plane import apply_rebind, inspect_rebind, rollback_rebind, verify_rebind
from runtime.session_store import SessionStore


def test_project_move_requires_explicit_rebind(tmp_path: Path) -> None:
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    old_root.mkdir(); new_root.mkdir()
    store = SessionStore(tmp_path / "control" / "sessions.sqlite3")
    session = store.create_session("demo", old_root, idempotency_key="session")
    assert inspect_rebind(store, session.session_id, new_root)["status"] == "PROJECT_ROOT_REBIND_REQUIRED"
    assert apply_rebind(store, session.session_id, new_root)["status"] == "REBOUND"
    assert inspect_rebind(store, session.session_id, new_root)["status"] == "CURRENT"
    assert verify_rebind(store, session.session_id, new_root)["status"] == "VERIFIED"
    assert rollback_rebind(store, session.session_id, old_root)["status"] == "ROLLED_BACK"
    assert verify_rebind(store, session.session_id, old_root)["status"] == "VERIFIED"
