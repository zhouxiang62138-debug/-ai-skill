"""F11.1 ExecutionBroker 最小验收。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionPathPolicy,
    ExecutionProfile,
    ExecutionRequest,
    ExecutionResult,
)
from runtime.leases import LeaseManager
from runtime.session_store import SessionStore


_CODE_HASH = "a" * 64
_ENV_HASH = "b" * 64


class RecordingEnvironment(ExecutionEnvironment):
    """只记录调用的无 Runtime 后端。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.execute_count = 0
        self.write_calls: list[str] = []

    def provision(self, context: ExecutionContext) -> None:
        return None

    def execute(
        self, context: ExecutionContext, request: ExecutionRequest
    ) -> ExecutionResult:
        self.execute_count += 1
        return ExecutionResult(exit_code=0, timed_out=False, stdout="ok")

    def read_file(self, context: ExecutionContext, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")

    def write_file(self, context: ExecutionContext, path: str, content: str) -> None:
        self.write_calls.append(path)
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_files(self, context: ExecutionContext, path: str = ".") -> list[str]:
        return []

    def snapshot(self, context: ExecutionContext) -> str:
        return "snapshot-test"

    def restore(self, context: ExecutionContext, snapshot_id: str) -> None:
        return None

    def terminate(self, context: ExecutionContext) -> None:
        return None


def _setup(tmp_path: Path, role: str = "generator"):
    project_root = tmp_path / "project"
    project_root.parent.mkdir(parents=True, exist_ok=True)
    project_root.mkdir()
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", project_root, idempotency_key="session")
    leases = LeaseManager(store)
    lease = leases.acquire(session.session_id, "worker-a")
    run_id = store.create_role_run(session.session_id, "worker-a", role)
    context = ExecutionContext(
        session_id=session.session_id,
        run_id=run_id,
        worker_id="worker-a",
        lease_version=lease.lease_version,
        role=role,
        project_id="demo",
        project_root=str(project_root),
    )
    request = ExecutionRequest(
        logical_call_id="logical-1",
        argv=("python", "-c", "print('ok')"),
        cwd=".",
        timeout=10,
        execution_profile=ExecutionProfile("test", _CODE_HASH, _ENV_HASH),
    )
    return store, leases, lease, context, request, RecordingEnvironment(project_root)


def test_backend_contract_does_not_depend_on_session_store() -> None:
    source = Path(__file__).parents[1] / "runtime" / "execution" / "base.py"
    assert "SessionStore" not in source.read_text(encoding="utf-8")
    assert "sqlite" not in source.read_text(encoding="utf-8").lower()


def test_broker_creates_completes_and_replays_one_tool_call(tmp_path: Path) -> None:
    store, _, lease, context, request, backend = _setup(tmp_path)
    broker = ExecutionBroker(store, LeaseManager(store))

    first = broker.execute(
        context, request, backend, lease_token=lease.lease_token or ""
    )
    second = broker.execute(
        context, request, backend, lease_token=lease.lease_token or ""
    )

    assert first.tool_call_id == second.tool_call_id
    assert first.result_hash == second.result_hash
    assert backend.execute_count == 1
    attempt = store.raw_connection().execute(
        "SELECT input_hash, code_snapshot_hash, environment_hash, result_hash "
        "FROM tool_attempts WHERE attempt_id=?",
        (first.attempt_id,),
    ).fetchone()
    assert attempt["input_hash"] != "unknown"
    assert attempt["code_snapshot_hash"] == _CODE_HASH
    assert attempt["environment_hash"] == _ENV_HASH
    assert attempt["result_hash"] == first.result_hash
    events = store.list_events(context.session_id)
    terminal = [
        event
        for event in events
        if event.event_type
        in {"TOOL_CALL_COMPLETED", "TOOL_CALL_FAILED", "TOOL_CALL_TIMED_OUT"}
    ]
    assert len(terminal) == 1


def test_same_argv_and_cwd_with_new_logical_id_creates_new_call(tmp_path: Path) -> None:
    store, _, lease, context, request, backend = _setup(tmp_path)
    broker = ExecutionBroker(store, LeaseManager(store))
    first = broker.execute(
        context, request, backend, lease_token=lease.lease_token or ""
    )
    second_request = ExecutionRequest(
        logical_call_id="logical-2",
        argv=request.argv,
        cwd=request.cwd,
        timeout=request.timeout,
        execution_profile=request.execution_profile,
    )
    second = broker.execute(
        context, second_request, backend, lease_token=lease.lease_token or ""
    )
    assert first.tool_call_id != second.tool_call_id
    rows = store.raw_connection().execute(
        "SELECT tool_call_id FROM tool_calls WHERE session_id=? ORDER BY requested_at",
        (context.session_id,),
    ).fetchall()
    assert len(rows) == 2
    assert backend.execute_count == 2


def test_generator_cannot_write_project_yaml(tmp_path: Path) -> None:
    store, _, lease, context, _, backend = _setup(tmp_path, "generator")
    broker = ExecutionBroker(store, LeaseManager(store))
    with pytest.raises(Exception, match="PROJECT_STATE_WRITE_REQUIRES_CAS"):
        broker.write_file(
            context,
            backend,
            "project.yaml",
            "status: BAD\n",
            lease_token=lease.lease_token or "",
        )
    assert backend.write_calls == []


def test_evaluator_and_planner_cannot_write_code(tmp_path: Path) -> None:
    for role in ("evaluator", "planner"):
        store, _, lease, context, _, backend = _setup(tmp_path / role, role)
        broker = ExecutionBroker(store, LeaseManager(store))
        with pytest.raises(Exception, match="EXECUTION_PATH_PROHIBITED"):
            broker.write_file(
                context,
                backend,
                "code/main.py",
                "bad",
                lease_token=lease.lease_token or "",
            )


def test_project_external_path_is_rejected(tmp_path: Path) -> None:
    policy = ExecutionPathPolicy()
    with pytest.raises(Exception, match="项目内相对路径"):
        policy.assert_path("generator", tmp_path, "../outside.txt", operation="write")
    with pytest.raises(Exception, match="项目内相对路径"):
        policy.assert_path(
            "generator", tmp_path, str(tmp_path / "outside.txt"), operation="write"
        )
