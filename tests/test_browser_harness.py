"""Stage 1 Browser Harness、Policy、Evidence 和 Gate 测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluation_evidence import evaluate_gates, validate_evidence_manifest  # noqa: E402
from runtime.browser import (  # noqa: E402
    BrowserBroker,
    BrowserEnvironmentBlocked,
    BrowserPolicy,
    BrowserPolicyError,
    BrowserHarness,
    BrowserProfile,
    evaluate_browser_gate,
    merge_browser_evidence,
)
from runtime.event_types import EventType  # noqa: E402
from runtime.execution.models import ExecutionContext  # noqa: E402
from runtime.leases import LeaseManager  # noqa: E402
from runtime.policy import CapabilityPolicy  # noqa: E402
from runtime.session_store import SessionStore  # noqa: E402


PROFILE = {
    "browser_validation": {
        "required": True,
        "base_url": "http://127.0.0.1:3000",
        "timeout_seconds": 1,
        "required_scenarios": ["todo-create"],
        "console_error_policy": "fail_on_error",
        "network_failure_policy": "fail_on_failure",
    }
}


class FakeBrowserAdapter:
    """不依赖 Playwright 的确定性 Driver。"""

    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.started = False
        self.url = "http://127.0.0.1:3000/"
        self.calls: list[tuple[str, Any]] = []

    def start(self) -> None:
        if self.blocked:
            raise BrowserEnvironmentBlocked("BROWSER_TEST_ENVIRONMENT_BLOCKED")
        self.started = True
        self.calls.append(("start", None))

    def navigate(self, url: str, *, timeout_ms: int) -> None:
        self.url = url
        self.calls.append(("navigate", url))

    def click(self, selector: str, *, timeout_ms: int) -> None:
        self.calls.append(("click", selector))

    def fill(self, selector: str, value: str, *, timeout_ms: int) -> None:
        self.calls.append(("fill", selector))

    def select(self, selector: str, value: str, *, timeout_ms: int) -> None:
        self.calls.append(("select", selector))

    def keyboard(self, key: str) -> None:
        self.calls.append(("keyboard", key))

    def wait_for_selector(self, selector: str, *, timeout_ms: int) -> None:
        self.calls.append(("wait_for_selector", selector))

    def wait_for_state(self, selector: str, state: str, *, timeout_ms: int) -> None:
        self.calls.append(("wait_for_state", (selector, state)))

    def read_visible_text(self, selector: str, *, timeout_ms: int) -> str:
        self.calls.append(("read_visible_text", selector))
        return "Saved token=do-not-store"

    def inspect_dom_state(self, selector: str, *, timeout_ms: int) -> dict[str, Any]:
        self.calls.append(("inspect_dom_state", selector))
        return {"count": 1, "visible": True, "text": "Saved"}

    def inspect_url(self) -> str:
        return self.url

    def screenshot(self, path: Path) -> None:
        path.write_bytes(b"fake-png")
        self.calls.append(("screenshot", path.as_posix()))

    def console_errors(self) -> list[str]:
        return []

    def failed_requests(self) -> list[str]:
        return []

    def close(self) -> None:
        self.started = False
        self.calls.append(("close", None))


def _profile() -> BrowserProfile:
    return BrowserProfile.from_mapping(PROFILE)


def test_browser_policy_is_profile_and_capability_gated() -> None:
    policy = BrowserPolicy()
    assert policy.authorize("evaluator", PROFILE).required is True
    with pytest.raises(BrowserPolicyError, match="BROWSER_PROFILE_NOT_REQUIRED"):
        policy.authorize("evaluator", {"browser_validation": {"required": False}})
    with pytest.raises(BrowserPolicyError, match="BROWSER_ROLE_DENIED"):
        policy.authorize("generator", PROFILE)
    with pytest.raises(BrowserPolicyError, match="BROWSER_URL_OUTSIDE_BASE_ORIGIN"):
        policy.assert_url("https://example.com/", PROFILE["browser_validation"]["base_url"])


def test_harness_records_all_operations_and_manifest_gate_passes(tmp_path: Path) -> None:
    adapter = FakeBrowserAdapter()
    harness = BrowserHarness(
        policy=BrowserPolicy(),
        profile=_profile(),
        evaluation_id="evaluation-001",
        browser_run_id="browser-run-001",
        requirement_id="REQ-001",
        acceptance_criterion_id="AC-001",
        scenario_id="todo-create",
        project_root=tmp_path,
        adapter=adapter,
    )
    harness.start()
    harness.navigate("http://127.0.0.1:3000/todos?secret=hidden")
    harness.click("[data-testid='save']")
    harness.fill("#title", "secret-value")
    harness.select("#priority", "high")
    harness.keyboard("Enter")
    harness.wait_for_selector("#saved")
    harness.wait_for_state("#saved", "visible")
    assert "[REDACTED]" in harness.read_visible_text("#saved")
    assert harness.inspect_dom_state("#saved")["visible"] is True
    assert harness.inspect_url() == "http://127.0.0.1:3000/todos"
    screenshot = harness.screenshot("evaluation/evidence/evaluation-001/browser-run-001.png")
    assert screenshot.endswith("browser-run-001.png")
    harness.close()

    bundle = harness.evidence_bundle()
    assert all(item["evaluation_id"] == "evaluation-001" for item in bundle["browser_evidence"])
    assert "secret-value" not in str(bundle)
    manifest = {
        "schema_version": "1.0",
        "evaluation_id": "evaluation-001",
        "created_at": "2026-08-09T00:00:00+08:00",
        "environment": {
            "os": "windows",
            "architecture": "x86_64",
            "python_version": "3.12",
            "working_directory": "code",
        },
        "commands": [],
        "artifacts": [],
        "checks": [],
        "gates": [],
    }
    manifest = merge_browser_evidence(manifest, bundle)
    assert validate_evidence_manifest(manifest) == []
    browser_input = evaluate_browser_gate(PROFILE, manifest)
    assert browser_input["result"] == "PASS"
    gates = evaluate_gates(
        {"GATE-BROWSER-ACCEPTANCE": browser_input},
        [{"id": "GATE-BROWSER-ACCEPTANCE", "required": True}],
    )
    assert gates[0]["result"] == "PASS"


def test_environment_blocked_is_not_implementation_pass(tmp_path: Path) -> None:
    harness = BrowserHarness(
        policy=BrowserPolicy(),
        profile=_profile(),
        evaluation_id="evaluation-001",
        browser_run_id="browser-run-001",
        requirement_id="REQ-001",
        acceptance_criterion_id="AC-001",
        scenario_id="todo-create",
        project_root=tmp_path,
        adapter=FakeBrowserAdapter(blocked=True),
    )
    with pytest.raises(BrowserEnvironmentBlocked):
        harness.start()
    harness.close()
    manifest = {**harness.evidence_bundle()}
    gate = evaluate_browser_gate(PROFILE, manifest)
    assert gate["result"] == "BLOCKED"
    assert gate["reason"] == "evaluation_environment_blocked"
    assert manifest["browser_runs"][0]["failure_class"] == "evaluation_environment_blocked"


def test_browser_gate_rejects_missing_manifest_and_wrong_requirement_mapping() -> None:
    profile_with_manifest = {
        "browser_validation": {
            **PROFILE["browser_validation"],
            "scenario_manifest": {"reference": "config/browser_scenarios/web_app.yaml"},
        }
    }
    manifest = {
        "browser_runs": [{
            "browser_run_id": "browser-run-001",
            "scenario_id": "todo-create",
            "result": "PASS",
        }],
        "browser_evidence": [{
            "browser_run_id": "browser-run-001",
            "step_id": "browser-run-001-step-001",
            "action": "click",
            "result": "PASS",
            "requirement_id": "WRONG",
            "acceptance_criterion_id": "WRONG",
        }],
    }
    with pytest.raises(Exception, match="MANIFEST"):
        evaluate_browser_gate(profile_with_manifest, manifest)
    scenario_manifest = {
        "schema_version": 1,
        "manifest_id": "web-app-core-v1",
        "profile": "web_app",
        "scenarios": [{
            "scenario_id": "todo-create",
            "requirement_id": "REQ-001",
            "acceptance_criterion_id": "AC-001",
            "preconditions": ["应用已启动"],
            "steps": [{"action": "click", "target": "[data-testid='save']"}],
            "expected_ui_state": {"saved": True},
            "expected_api_state": {"business_operation": "observed"},
            "critical_workflow": True,
            "scenario_type": "normal",
        }],
    }
    gate = evaluate_browser_gate(PROFILE, manifest, scenario_manifest)
    assert gate["result"] == "FAIL"
    assert "trace" in gate["reason"]


def _broker_setup(tmp_path: Path, role: str = "evaluator") -> tuple[BrowserBroker, ExecutionContext, str, SessionStore]:
    root = tmp_path / "project"
    root.mkdir()
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
    return BrowserBroker(store, leases), context, lease.lease_token or "", store


def test_browser_broker_reuses_session_lease_and_capability(tmp_path: Path) -> None:
    broker, context, token, _ = _broker_setup(tmp_path)
    harness = broker.create(
        context,
        profile=PROFILE,
        evaluation_id="evaluation-001",
        browser_run_id="browser-run-001",
        requirement_id="REQ-001",
        acceptance_criterion_id="AC-001",
        lease_token=token,
        adapter=FakeBrowserAdapter(),
    )
    harness.start()
    harness.close()
    assert harness.run_record().result == "PASS"


def test_broker_denied_capability_is_audited_without_secret(tmp_path: Path) -> None:
    broker, context, token, store = _broker_setup(tmp_path)
    policy_path = tmp_path / "role_policies.yaml"
    policy_path.write_text(
        """capabilities:\n  - browser.access\nroles:\n  evaluator:\n    capabilities: []\n""",
        encoding="utf-8",
    )
    denied = CapabilityPolicy(policy_path)
    from runtime.browser.policy import BrowserPolicy

    broker = BrowserBroker(store, LeaseManager(store), capability_policy=denied, browser_policy=BrowserPolicy(capability_policy=denied))
    with pytest.raises(Exception, match="CAPABILITY_DENIED"):
        broker.create(
            context,
            profile=PROFILE,
            evaluation_id="evaluation-001",
            browser_run_id="browser-run-001",
            requirement_id="REQ-001",
            acceptance_criterion_id="AC-001",
            lease_token=token,
        )
    events = [event for event in store.list_events(context.session_id) if event.event_type == EventType.CAPABILITY_DENIED]
    assert len(events) == 1
    assert "secret" not in str(events[0].payload).lower()
