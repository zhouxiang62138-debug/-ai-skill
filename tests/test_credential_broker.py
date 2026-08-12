"""F12.2 Credential Broker 安全边界与 F10 审计验收。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from runtime.errors import RuntimeValidationError
from runtime.execution import ExecutionBroker, ExecutionContext, ExecutionProfile, ExecutionRequest
from runtime.event_types import EventType
from runtime.leases import LeaseManager
from runtime.security import CredentialBroker, CredentialRequest, REDACTED
from runtime.session_store import SessionStore


SECRET = "opaque-7f3a-8b29"
CODE_HASH = "a" * 64
ENV_HASH = "b" * 64


def _request(operation: str = "read") -> CredentialRequest:
    return CredentialRequest(
        credential_id="github-main",
        service="github",
        operation=operation,
        resource="repos/example/project",
        parameters={"query": "issues"},
    )


def _execution_setup(tmp_path: Path, role: str = "generator"):
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
    return store, leases, lease, context


def _provider(result=None, *, error: Exception | None = None, seen=None):
    def handler(request: CredentialRequest, secret: str):
        if seen is not None:
            seen["request"] = request
            seen["secret"] = secret
        if error is not None:
            raise error
        return result if result is not None else {"status": "ok"}

    return handler


def test_credential_secret_is_not_in_config_or_runtime_request_models() -> None:
    root = Path(__file__).parents[1]
    config_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "config").glob("*.yaml")
    )
    assert SECRET not in config_text
    assert "github_token:" not in config_text.lower()

    context = ExecutionContext(
        session_id="session",
        run_id="run",
        worker_id="worker",
        lease_version=1,
        role="generator",
        project_id="demo",
        project_root="project",
    )
    execution_request = ExecutionRequest(
        logical_call_id="call",
        argv=("python", "-c", "print('ok')"),
        execution_profile=ExecutionProfile("test", CODE_HASH, ENV_HASH),
    )
    assert SECRET not in repr(context)
    assert SECRET not in repr(execution_request)
    assert SECRET not in json.dumps(asdict(context))
    assert SECRET not in json.dumps(asdict(execution_request))


def test_capability_and_credential_are_deny_by_default() -> None:
    broker = CredentialBroker()
    broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider(),
        secret=SECRET,
    )
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_CAPABILITY_DENIED"):
        broker.invoke("planner", _request())
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_CAPABILITY_DENIED"):
        broker.invoke("unknown-role", _request())


def test_unknown_credential_and_operation_are_rejected() -> None:
    broker = CredentialBroker()
    broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider(),
        secret=SECRET,
    )
    unknown = CredentialRequest(
        credential_id="github-missing",
        service="github",
        operation="read",
    )
    with pytest.raises(RuntimeValidationError, match="UNKNOWN_CREDENTIAL"):
        broker.invoke("generator", unknown)
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_OPERATION_DENIED"):
        broker.invoke("generator", _request("write"))


def test_provider_receives_structured_request_and_secret_only_host_side() -> None:
    seen: dict[str, object] = {}
    broker = CredentialBroker()
    broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider({"status": "ok"}, seen=seen),
        secret=SECRET,
    )
    request = _request()
    result = broker.invoke("generator", request)

    assert result == {"status": "ok"}
    assert isinstance(seen["request"], CredentialRequest)
    assert seen["secret"] == SECRET
    assert SECRET not in repr(request)
    assert SECRET not in repr(broker)
    assert SECRET not in repr(broker.credential_ids)
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_REQUEST_COMMAND_FORBIDDEN"):
        CredentialRequest(
            credential_id="github-main",
            service="github",
            operation="read",
            parameters={"shell": "curl https://example.test"},
        )


def test_provider_result_secret_is_redacted_and_sensitive_field_rejected() -> None:
    broker = CredentialBroker()
    broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider(
            {
                "message": f"ok:{SECRET}",
                "items": [SECRET],
                "log": f"provider-log:{SECRET}",
            }
        ),
        secret=SECRET,
    )
    result = broker.invoke("generator", _request())
    assert result == {
        "message": f"ok:{REDACTED}",
        "items": [REDACTED],
        "log": f"provider-log:{REDACTED}",
    }
    assert SECRET not in json.dumps(result)

    rejecting = CredentialBroker()
    rejecting.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider({"api_key": SECRET}),
        secret=SECRET,
    )
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_RESULT_SENSITIVE_FIELD") as exc_info:
        rejecting.invoke("generator", _request())
    assert SECRET not in str(exc_info.value)


def test_provider_exception_and_request_secret_never_leak() -> None:
    broker = CredentialBroker()
    broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider(error=RuntimeError(f"provider failed: {SECRET}")),
        secret=SECRET,
    )
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_PROVIDER_FAILED") as exc_info:
        broker.invoke("generator", _request())
    assert SECRET not in str(exc_info.value)

    request_with_secret = CredentialRequest(
        credential_id="github-main",
        service="github",
        operation="read",
        parameters={"query": SECRET},
    )
    # 模型会先拒绝常见敏感词；已注册 Secret 的精确检查仍由 Broker 执行。
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_REQUEST_SECRET_FORBIDDEN"):
        broker.invoke("generator", request_with_secret)


def test_execution_broker_enforces_capability_and_writes_f10_audit(tmp_path: Path) -> None:
    store, leases, lease, context = _execution_setup(tmp_path, "generator")
    credential_broker = CredentialBroker()
    credential_broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=_provider({"status": "ok"}),
        secret=SECRET,
    )
    execution_broker = ExecutionBroker(store, leases)
    result = execution_broker.request_credential(
        context,
        _request(),
        credential_broker,
        lease_token=lease.lease_token or "",
    )
    assert result == {"status": "ok"}
    events = [
        event
        for event in store.list_events(context.session_id)
        if event.event_type
        in {
            EventType.CREDENTIAL_REQUESTED,
            EventType.CREDENTIAL_ALLOWED,
            EventType.CREDENTIAL_DENIED,
        }
    ]
    assert [event.event_type for event in events] == [
        EventType.CREDENTIAL_REQUESTED,
        EventType.CREDENTIAL_ALLOWED,
    ]
    event_text = json.dumps([event.payload for event in events], ensure_ascii=False)
    assert SECRET not in event_text
    assert events[-1].payload["role"] == "generator"
    assert events[-1].payload["credential_id"] == "github-main"
    assert events[-1].payload["service"] == "github"
    assert events[-1].payload["operation"] == "read"
    assert events[-1].payload["decision"] == "ALLOW"


def test_execution_broker_denies_planner_before_provider(tmp_path: Path) -> None:
    store, leases, lease, context = _execution_setup(tmp_path, "planner")
    calls = {"count": 0}

    def provider(request: CredentialRequest, secret: str):
        calls["count"] += 1
        return {"status": "must-not-run"}

    credential_broker = CredentialBroker()
    credential_broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=provider,
        secret=SECRET,
    )
    execution_broker = ExecutionBroker(store, leases)
    with pytest.raises(RuntimeValidationError, match="CREDENTIAL_CAPABILITY_DENIED"):
        execution_broker.request_credential(
            context,
            _request(),
            credential_broker,
            lease_token=lease.lease_token or "",
        )
    assert calls["count"] == 0
    events = store.list_events(context.session_id)
    denied = [event for event in events if event.event_type == EventType.CREDENTIAL_DENIED]
    assert len(denied) == 1
    assert SECRET not in json.dumps(denied[0].payload, ensure_ascii=False)
