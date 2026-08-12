"""F12.3 Network / External Tool Policy 验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.errors import RuntimeValidationError
from runtime.event_types import EventType
from runtime.execution import ExecutionBroker, ExecutionContext
from runtime.leases import LeaseManager
from runtime.security import (
    CredentialBroker,
    CredentialRequest,
    ExternalToolPolicy,
    ExternalToolRequest,
    NetworkPolicy,
    NetworkRequest,
    authorize_redirect,
)
from runtime.session_store import SessionStore


SECRET = "opaque-7f3a-8b29"


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


def _credential_broker(calls: dict[str, int] | None = None) -> CredentialBroker:
    def provider(request: CredentialRequest, secret: str):
        if calls is not None:
            calls["count"] += 1
        assert secret == SECRET
        return {"status": "authorized"}

    broker = CredentialBroker()
    broker.register(
        credential_id="github-main",
        service="github",
        allowed_operations={"read"},
        provider=provider,
        secret=SECRET,
    )
    return broker


def _credential_request() -> CredentialRequest:
    return CredentialRequest(
        credential_id="github-main",
        service="github",
        operation="read",
        resource="repos/example/project",
    )


def test_network_policy_is_deny_by_default_and_capability_first() -> None:
    policy = NetworkPolicy()
    with pytest.raises(RuntimeValidationError, match="NETWORK_CAPABILITY_DENIED"):
        policy.authorize("planner", "github", "read", "https://api.github.com")
    with pytest.raises(RuntimeValidationError, match="NETWORK_CAPABILITY_DENIED"):
        policy.authorize("unknown-role", "github", "read", "https://api.github.com")
    assert policy.check("generator", "unknown-service", "read", "https://api.github.com") == "DENY"


def test_network_exact_host_and_operation_allowlist() -> None:
    policy = NetworkPolicy()
    assert (
        policy.authorize(
            "generator", "github", "read", "https://api.github.com/repos/example"
        )
        == "ALLOW"
    )
    with pytest.raises(RuntimeValidationError, match="NETWORK_HOST_DENIED"):
        policy.authorize(
            "generator", "github", "read", "https://api.github.com.attacker.com"
        )
    with pytest.raises(RuntimeValidationError, match="NETWORK_OPERATION_DENIED"):
        policy.authorize("generator", "github", "write", "https://api.github.com")
    with pytest.raises(RuntimeValidationError, match="NETWORK_PORT_DENIED"):
        policy.authorize("generator", "github", "read", "https://api.github.com:444")


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://10.0.0.1",
        "https://192.168.1.1",
        "https://169.254.169.254",
        "https://0.0.0.0",
        "https://metadata.google.internal",
    ],
)
def test_network_private_local_link_local_and_metadata_hosts_are_blocked(url: str) -> None:
    with pytest.raises(RuntimeValidationError, match="NETWORK_PRIVATE_HOST_DENIED"):
        NetworkPolicy().authorize("generator", "github", "read", url)


@pytest.mark.parametrize("scheme", ["file", "ftp", "data", "javascript"])
def test_network_forbidden_schemes_are_denied(scheme: str) -> None:
    url = f"{scheme}://api.github.com/resource"
    with pytest.raises(RuntimeValidationError, match="NETWORK_SCHEME_DENIED"):
        NetworkPolicy().authorize("generator", "github", "read", url)


def test_network_url_credentials_and_sensitive_query_are_denied() -> None:
    policy = NetworkPolicy()
    with pytest.raises(RuntimeValidationError, match="NETWORK_URL_CREDENTIALS_FORBIDDEN"):
        policy.authorize("generator", "github", "read", "https://user:pass@api.github.com")
    with pytest.raises(RuntimeValidationError, match="NETWORK_SENSITIVE_QUERY_FORBIDDEN"):
        policy.authorize(
            "generator",
            "github",
            "read",
            "https://api.github.com?access_token=opaque-7f3a-8b29",
        )


def test_redirect_target_is_reauthorized() -> None:
    policy = NetworkPolicy()
    assert authorize_redirect(
        policy,
        "generator",
        "github",
        "read",
        "https://api.github.com/redirected",
    ) == "ALLOW"
    with pytest.raises(RuntimeValidationError, match="NETWORK_HOST_DENIED"):
        authorize_redirect(
            policy,
            "generator",
            "github",
            "read",
            "https://api.github.com.attacker.com/redirected",
        )


def test_external_tool_requires_tool_and_operation_allowlist() -> None:
    policy = ExternalToolPolicy()
    assert policy.authorize("generator", "git", "read") == "ALLOW"
    with pytest.raises(RuntimeValidationError, match="UNKNOWN_EXTERNAL_TOOL"):
        policy.authorize("generator", "curl", "read")
    with pytest.raises(RuntimeValidationError, match="EXTERNAL_TOOL_OPERATION_DENIED"):
        policy.authorize("generator", "git", "write")
    with pytest.raises(RuntimeValidationError, match="EXTERNAL_TOOL_CAPABILITY_DENIED"):
        policy.authorize("planner", "git", "read")
    with pytest.raises(RuntimeValidationError, match="EXTERNAL_TOOL_COMMAND_FORBIDDEN"):
        ExternalToolRequest("git", "read", "repo;rm -rf")


def test_network_and_tool_audit_payloads_are_safe() -> None:
    events: list[tuple[EventType, dict[str, str]]] = []
    policy = NetworkPolicy()
    audit = lambda event_type, payload: events.append((event_type, dict(payload)))
    policy.authorize(
        "generator",
        "github",
        "read",
        "https://api.github.com/repos/example",
        audit=audit,
    )
    with pytest.raises(RuntimeValidationError):
        policy.authorize(
            "generator",
            "github",
            "read",
            "https://api.github.com.attacker.com",
            audit=audit,
        )
    assert events[0][0] == EventType.NETWORK_ALLOWED
    assert events[-1][0] == EventType.NETWORK_DENIED
    assert all(SECRET not in json.dumps(payload) for _, payload in events)
    assert events[0][1]["host"] == "api.github.com"
    assert events[0][1]["capability"] == "network.access"


def test_execution_broker_network_policy_reuses_f10_events(tmp_path: Path) -> None:
    store, leases, lease, context = _execution_setup(tmp_path)
    broker = ExecutionBroker(store, leases)
    result = broker.request_network(
        context,
        NetworkRequest("github", "read", "https://api.github.com/repos/example"),
        NetworkPolicy(),
        lease_token=lease.lease_token or "",
    )
    assert result == "ALLOW"
    events = [
        event for event in store.list_events(context.session_id)
        if event.event_type == EventType.NETWORK_ALLOWED
    ]
    assert len(events) == 1
    assert events[0].payload["host"] == "api.github.com"
    assert SECRET not in json.dumps(events[0].payload)


def test_external_policy_runs_before_credential_broker(tmp_path: Path) -> None:
    calls = {"count": 0}
    store, leases, lease, context = _execution_setup(tmp_path)
    execution_broker = ExecutionBroker(store, leases)
    with pytest.raises(RuntimeValidationError, match="EXTERNAL_TOOL_OPERATION_DENIED"):
        execution_broker.invoke_external(
            context,
            ExternalToolRequest("git", "write"),
            ExternalToolPolicy(),
            lambda request, credential: {"status": "must-not-run"},
            lease_token=lease.lease_token or "",
            credential_broker=_credential_broker(calls),
            credential_request=_credential_request(),
        )
    assert calls["count"] == 0


def test_external_allowed_then_credential_then_handler(tmp_path: Path) -> None:
    store, leases, lease, context = _execution_setup(tmp_path)
    execution_broker = ExecutionBroker(store, leases)

    def handler(request: ExternalToolRequest, credential: dict[str, str] | None):
        assert request.tool == "git"
        assert credential == {"status": "authorized"}
        assert SECRET not in json.dumps(credential)
        return {"status": "ok"}

    result = execution_broker.invoke_external(
        context,
        ExternalToolRequest("git", "read", "repository"),
        ExternalToolPolicy(),
        handler,
        lease_token=lease.lease_token or "",
        credential_broker=_credential_broker(),
        credential_request=_credential_request(),
    )
    assert result == {"status": "ok"}
    events = [
        event for event in store.list_events(context.session_id)
        if event.event_type
        in {
            EventType.EXTERNAL_TOOL_ALLOWED,
            EventType.CREDENTIAL_REQUESTED,
            EventType.CREDENTIAL_ALLOWED,
        }
    ]
    assert [event.event_type for event in events] == [
        EventType.EXTERNAL_TOOL_ALLOWED,
        EventType.CREDENTIAL_REQUESTED,
        EventType.CREDENTIAL_ALLOWED,
    ]
    assert SECRET not in json.dumps([event.payload for event in events])


def test_external_handler_exception_is_stable_and_secret_free(tmp_path: Path) -> None:
    store, leases, lease, context = _execution_setup(tmp_path)
    execution_broker = ExecutionBroker(store, leases)

    def handler(request: ExternalToolRequest, credential):
        raise RuntimeError(f"handler failed: {SECRET}")

    with pytest.raises(RuntimeValidationError, match="EXTERNAL_TOOL_PROVIDER_FAILED") as exc_info:
        execution_broker.invoke_external(
            context,
            ExternalToolRequest("git", "read"),
            ExternalToolPolicy(),
            handler,
            lease_token=lease.lease_token or "",
        )
    assert SECRET not in str(exc_info.value)
