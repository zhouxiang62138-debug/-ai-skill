from pathlib import Path

from runtime.session_store import SessionStore


def test_failed_role_run_is_not_completed(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", tmp_path, idempotency_key="session")
    run_id = store.create_role_run(session.session_id, "worker-a", "planner")
    store.fail_role_run(session.session_id, run_id, {"reason": "disk_error"})
    row = store.raw_connection().execute("SELECT status FROM role_runs WHERE run_id=?", (run_id,)).fetchone()
    assert row["status"] == "FAILED"
