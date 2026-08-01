from pathlib import Path

from runtime.session_store import SessionStore


def test_recover_started_tool_marks_unknown_after_crash(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", tmp_path, idempotency_key="session")
    call = store.request_tool_call(session.session_id, tool_name="test", arguments={}, idempotency_key="one")
    store.start_tool_call(session.session_id, call)

    assert store.recover_interrupted_tool_calls(session.session_id) == [call]
    row = store.raw_connection().execute("SELECT status FROM tool_calls WHERE tool_call_id=?", (call,)).fetchone()
    assert row["status"] == "UNKNOWN_AFTER_CRASH"
