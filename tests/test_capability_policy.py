"""F12.1 Capability Policy 与 ExecutionBroker 集成验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.errors import RuntimeValidationError
from runtime.execution import (
    ExecutionBroker,
    ExecutionContext,
    ExecutionEnvironment,
    ExecutionProfile,
    ExecutionRequest,
    ExecutionResult,
)
from runtime.policy import (
    CAPABILITY_ALLOW,
    CAPABILITY_DENY,
    CapabilityPolicy,
    load_capabilities,
    load_role_capabilities,
)
from runtime.leases import LeaseManager
from runtime.session_store import SessionStore


_CODE_HASH = "a" * 64
_ENV_HASH = "b" * 64


class CountingEnvironment(ExecutionEnvironment):
    """记录 Broker 是否越过 Capability Gate。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.execute_count = 0
        self.read_count = 0
        self.write_count = 0

    def provision(self, context: ExecutionContext) -> None:
        return None

    def execute(
        self, context: ExecutionContext, request: ExecutionRequest
    ) -> ExecutionResult:
        self.execute_count += 1
        return ExecutionResult(exit_code=0, timed_out=False, stdout="ok")

    def read_file(self, context: ExecutionContext, path: str) -> str:
        self.read_count += 1
        return (self.root / path).read_text(encoding="utf-8")

    def write_file(self, context: ExecutionContext, path: str, content: str) -> None:
        self.write_count += 1
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
    root = tmp_path / "project"
    root.mkdir(parents=True)
    store = SessionStore(tmp_path / "sessions.sqlite3")
    session = store.create_session("demo", root, idempotency_key="session")
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
        project_root=str(root),
    )
    request = ExecutionRequest(
        logical_call_id="logical-1",
        argv=("python", "-c", "print('ok')"),
        execution_profile=ExecutionProfile("test", _CODE_HASH, _ENV_HASH),
    )
    return store, leases, lease, context, request, CountingEnvironment(root)


def _minimal_policy(tmp_path: Path) -> CapabilityPolicy:
    config = tmp_path / "role_policies.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        """capabilities:
  - filesystem.read
  - filesystem.write
  - process.execute
roles:
  planner:
    capabilities:
      - filesystem.read
  generator:
    capabilities:
      - filesystem.read
  evaluator:
    capabilities:
      - filesystem.read
""",
        encoding="utf-8",
    )
    return CapabilityPolicy(config)


def test_capability_policy_is_deny_by_default_and_config_driven(tmp_path: Path) -> None:
    policy = CapabilityPolicy()
    assert policy.authorize("generator", "process.execute") == CAPABILITY_ALLOW
    assert policy.check("planner", "process.execute") == CAPABILITY_DENY
    with pytest.raises(RuntimeValidationError, match="UNKNOWN_ROLE"):
        policy.authorize("architect", "process.execute")
    with pytest.raises(RuntimeValidationError, match="UNKNOWN_CAPABILITY"):
        policy.authorize("generator", "unknown.capability")

    configured = _minimal_policy(tmp_path)
    assert configured.check("generator", "process.execute") == CAPABILITY_DENY
    with pytest.raises(RuntimeValidationError, match="CAPABILITY_DENIED"):
        configured.authorize("generator", "process.execute")


def test_role_capabilities_match_existing_responsibilities() -> None:
    capabilities = load_capabilities()
    roles = load_role_capabilities()
    assert {"planner", "generator", "evaluator"}.issubset(roles)
    assert roles["planner"] == {"filesystem.read", "filesystem.write"}
    assert "process.execute" not in roles["planner"]
    assert "process.execute" in roles["generator"]
    assert roles["evaluator"] >= {
        "filesystem.read",
        "filesystem.write",
        "process.execute",
    }
    assert all(item in capabilities for values in roles.values() for item in values)
    assert all("token" not in item.lower() for item in capabilities)


def test_capability_context_rejects_secret_like_values() -> None:
    with pytest.raises(RuntimeValidationError, match="CAPABILITY_SECRET_FORBIDDEN"):
        CapabilityPolicy().authorize(
            "generator",
            "network.access",
            resource="token=real-secret",
        )


def test_execution_broker_checks_process_capability_and_audits_deny(tmp_path: Path) -> None:
    store, leases, lease, context, request, backend = _setup(tmp_path, "planner")
    broker = ExecutionBroker(store, leases)

    with pytest.raises(RuntimeValidationError, match="CAPABILITY_DENIED"):
        broker.execute(
            context,
            request,
            backend,
            lease_token=lease.lease_token or "",
        )

    assert backend.execute_count == 0
    denied = [
        event
        for event in store.list_events(context.session_id)
        if event.event_type == "CAPABILITY_DENIED"
    ]
    assert len(denied) == 1
    payload = json.dumps(denied[0].payload, ensure_ascii=False)
    assert denied[0].payload["capability"] == "process.execute"
    assert denied[0].payload["decision"] == "DENY"
    assert "secret" not in payload.lower()


def test_capability_deny_event_never_contains_secret_context(tmp_path: Path) -> None:
    store, leases, lease, context, _, backend = _setup(tmp_path)
    broker = ExecutionBroker(store, leases)

    with pytest.raises(RuntimeValidationError, match="CAPABILITY_SECRET_FORBIDDEN"):
        broker.read_file(
            context,
            backend,
            "token=real-secret",
            lease_token=lease.lease_token or "",
        )

    denied = [
        event
        for event in store.list_events(context.session_id)
        if event.event_type == "CAPABILITY_DENIED"
    ]
    assert len(denied) == 1
    payload = json.dumps(denied[0].payload, ensure_ascii=False)
    assert "real-secret" not in payload
    assert "token=real-secret" not in payload


def test_execution_broker_checks_filesystem_capabilities(tmp_path: Path) -> None:
    store, leases, lease, context, _, backend = _setup(tmp_path)
    policy = _minimal_policy(tmp_path / "policy")
    broker = ExecutionBroker(store, leases, capability_policy=policy)
    (Path(context.project_root) / "code" / "readme.txt").parent.mkdir()
    (Path(context.project_root) / "code" / "readme.txt").write_text(
        "ok", encoding="utf-8"
    )

    assert broker.read_file(
        context,
        backend,
        "code/readme.txt",
        lease_token=lease.lease_token or "",
    ) == "ok"
    with pytest.raises(RuntimeValidationError, match="CAPABILITY_DENIED"):
        broker.write_file(
            context,
            backend,
            "code/main.py",
            "blocked",
            lease_token=lease.lease_token or "",
        )
    assert backend.read_count == 1
    assert backend.write_count == 0
