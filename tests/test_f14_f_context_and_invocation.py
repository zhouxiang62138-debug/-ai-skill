"""F14-F Role Context 闭包和 Invocation Gate 测试。"""

import pytest

from runtime.f14_roles import RoleContextScopeBuilder
from runtime.context.models import ContextPackage, ContextSource
from runtime.f14_control import (
    F14ContextDeliveryService,
    F14FeatureFlags,
    F14RolloutPolicy,
)
from runtime.invocation_gate import (
    BLOCKED,
    LLM_REQUIRED,
    PYTHON_ONLY,
    InvocationGate,
    InvocationGateRequest,
)
from runtime.deterministic.telemetry import RuntimeTelemetry


def test_generator_rework_scope_reports_missing_mandatory_without_guessing() -> None:
    scope = RoleContextScopeBuilder().build(
        role="generator",
        phase="rework",
        available={"project_state_summary": "state", "evaluation_issue": "issue"},
    )
    assert not scope.complete
    assert "approved_plan_task" in scope.missing_mandatory
    assert scope.unknown == ()


def test_evaluator_scope_is_independent_and_has_broader_boundary() -> None:
    available = {
        "independent_mandatory_coverage": "coverage",
        "approved_scope": "scope",
        "relevant_requirements": "requirements",
        "relevant_acceptance_criteria": "ac",
        "current_revision": "revision",
        "current_code_snapshot": "code",
        "mandatory_regression": "regression",
        "broader_regression_boundary": "boundary",
        "independent_evidence": "evidence",
        "evaluation_profile": "profile",
        "changed_files": "files",
        "issue_map": "issues",
    }
    scope = RoleContextScopeBuilder().build(role="evaluator", phase="evaluation", available=available)
    assert scope.complete
    assert "broader_regression_boundary" in scope.delivered


def test_invocation_gate_allows_only_explicitly_deterministic_python_work() -> None:
    gate = InvocationGate()
    decision = gate.evaluate(
        InvocationGateRequest(
            role="generator",
            phase="rework",
            task_kind="hash",
            project_revision=4,
            evidence_refs=("runtime:hash",),
        )
    )
    assert decision.result == PYTHON_ONLY
    assert decision.real_model_request is False
    assert len(decision.input_hash) == 64


@pytest.mark.parametrize("task_kind", ["product_decision", "bug_diagnosis", "evaluation_judgment"])
def test_invocation_gate_keeps_semantic_tasks_on_llm(task_kind: str) -> None:
    decision = InvocationGate().evaluate(
        InvocationGateRequest(role="generator", phase="implementation", task_kind=task_kind)
    )
    assert decision.result == LLM_REQUIRED


def test_invocation_gate_never_reuses_evaluator_verdict() -> None:
    decision = InvocationGate().evaluate(
        InvocationGateRequest(
            role="evaluator",
            phase="evaluation",
            task_kind="evaluation_judgment",
            previous_verdict={"result": "PASS"},
        )
    )
    assert decision.result == LLM_REQUIRED
    assert decision.reason == "semantic_result_reuse_forbidden"


def test_invocation_gate_blocks_invalid_security_boundary() -> None:
    execution = InvocationGate().execute(
        InvocationGateRequest(
            role="generator",
            phase="rework",
            task_kind="hash",
            security_valid=False,
        )
    )
    assert execution.decision.result == BLOCKED
    assert execution.lifecycle["real_model_request"] is False


def test_context_delivery_service_delivers_selected_package_only_in_canary() -> None:
    def digest(label: str) -> str:
        import hashlib
        return hashlib.sha256(label.encode()).hexdigest()

    sources = tuple(
        ContextSource(
            source_type="test",
            reference=reference,
            content_hash=digest(reference),
            reason="test source",
            priority="REQUIRED" if reference == "requirements" else "NORMAL",
            delivery_mode="INLINE",
            size=size,
            original_size=size,
            included_size=size,
            inline_content=reference,
        )
        for reference, size in (("requirements", 100), ("unrelated", 300))
    )
    current = ContextPackage(
        context_id="context-current",
        session_id="session",
        run_id="run",
        role="generator",
        project_id="test_project",
        workflow_state="IMPLEMENTATION",
        project_revision=1,
        created_at="2026-01-01T00:00:00Z",
        project_state_hash=digest("state"),
        context_policy_hash=digest("policy"),
        budget_fingerprint=digest("budget"),
        sources=sources,
        omitted_sources=(),
        budget_limit=1000,
        budget_used=400,
        inline_bytes=400,
        source_count=2,
        inline_source_count=2,
        reference_source_count=0,
        omitted_source_count=0,
        max_inline_bytes=1000,
        max_source_inline_bytes=1000,
        max_sources=10,
        context_hash=digest("context"),
    )
    flags = F14FeatureFlags(
        context_delivery_mode="f14_selective_canary",
        selective_context_enabled=True,
        selective_roles=("generator",),
        selective_phases=("rework",),
        qualification_evidence={"controlled": "PASS"},
        rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
    )
    delivered, decision = F14ContextDeliveryService(flags).deliver(
        current,
        role="generator",
        phase="rework",
        selected_references=("requirements",),
        mandatory_references=("requirements",),
        test_project=True,
    )
    assert decision.result == "f14_selective_canary"
    assert delivered.inline_bytes == 100
    assert [source.reference for source in delivered.sources] == ["requirements"]


def test_telemetry_distinguishes_gross_and_net_context_reduction() -> None:
    telemetry = RuntimeTelemetry()
    telemetry.record_selective_delivery(
        f13_full_bytes=1000,
        initial_selective_bytes=400,
        expansion_bytes=100,
        recovery_additional_bytes=50,
        result="f14_selective_canary",
    )
    assert telemetry.context_efficiency["gross_reduction_ratio"] == 0.6
    assert telemetry.context_efficiency["net_reduction_ratio"] == 0.45
    assert telemetry.context_efficiency["net_context_bytes"] == 550


def test_invocation_gate_telemetry_does_not_count_decision_as_real_request() -> None:
    telemetry = RuntimeTelemetry()
    telemetry.record_invocation_gate(result="LLM_REQUIRED", role="generator", phase="implementation")
    assert telemetry.model_efficiency["actual_model_requests"] == 0
    assert telemetry.execution_types["invocation_gate:LLM_REQUIRED"] == 1
