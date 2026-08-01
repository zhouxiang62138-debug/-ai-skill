from pathlib import Path

from runtime.session_store import SessionStore
from runtime.errors import RuntimeStorageError


def test_recover_started_tool_marks_unknown_after_crash(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", tmp_path, idempotency_key="session")
    call = store.request_tool_call(session.session_id, tool_name="test", arguments={}, idempotency_key="one")
    store.start_tool_call(session.session_id, call)

    assert store.recover_interrupted_tool_calls(session.session_id) == [call]
    row = store.raw_connection().execute("SELECT status FROM tool_calls WHERE tool_call_id=?", (call,)).fetchone()
    assert row["status"] == "UNKNOWN_AFTER_CRASH"


def test_tool_request_and_event_roll_back_together(tmp_path: Path, monkeypatch) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", tmp_path, idempotency_key="session")

    def fail_event(*_args, **_kwargs):
        raise RuntimeStorageError("EVENT_WRITE_FAILED")

    monkeypatch.setattr(store, "_append_event_in_transaction", fail_event)
    try:
        store.request_tool_call(
            session.session_id,
            tool_name="test",
            arguments={},
            idempotency_key="atomic-request",
        )
    except RuntimeStorageError as error:
        assert str(error) == "EVENT_WRITE_FAILED"
    else:  # pragma: no cover - 该路径表示原子性失效
        raise AssertionError("工具请求应因事件写入失败回滚")

    row = store.raw_connection().execute(
        "SELECT 1 FROM tool_calls WHERE session_id=?", (session.session_id,)
    ).fetchone()
    assert row is None


def test_same_command_after_code_change_creates_new_attempt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", tmp_path, idempotency_key="session")
    call = store.request_tool_call(session.session_id, tool_name="test", arguments={}, idempotency_key="one")
    first = store.start_tool_call(session.session_id, call, code_snapshot_hash="code-a", environment_hash="env-a")
    store.complete_tool_call(session.session_id, call, result_reference="tool-results/a.json", result_hash="a")
    second = store.start_tool_call(session.session_id, call, code_snapshot_hash="code-b", environment_hash="env-a")

    assert first != second
    rows = store.raw_connection().execute(
        "SELECT attempt_id FROM tool_attempts WHERE tool_call_id=?", (call,)
    ).fetchall()
    assert len(rows) == 2


def test_tool_timeout_is_not_succeeded(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", tmp_path, idempotency_key="session")
    call = store.request_tool_call(session.session_id, tool_name="test", arguments={}, idempotency_key="timeout")
    attempt = store.start_tool_call(session.session_id, call)
    store.complete_tool_call(
        session.session_id,
        call,
        result_reference="tool-results/timeout.json",
        result_hash="timeout-hash",
        status="TIMED_OUT",
        attempt_id=attempt,
    )

    row = store.raw_connection().execute(
        "SELECT status FROM tool_calls WHERE tool_call_id=?", (call,)
    ).fetchone()
    assert row["status"] == "TIMED_OUT"
    assert store.completed_tool_calls(session.session_id) == []
