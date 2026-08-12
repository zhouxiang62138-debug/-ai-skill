"""F14-C4 强制 Coverage Gate 的 fail-closed / fail-safe 测试。"""

from __future__ import annotations

import pytest

from runtime.context import (
    ContextUnit,
    EnforcedCoverageGate,
    EnforcementDecision,
    ShadowClassifier,
    ShadowComparisonBuilder,
)
from runtime.errors import RuntimeValidationError
from tests.test_f14_c_shadow_coverage import _semantic, _unit


def test_c4_incomplete_mandatory_coverage_blocks_before_model_call() -> None:
    semantic = _semantic(global_constraints=("SECURITY-002",))
    current = [_unit("REQ-007", "mandatory")]
    classification = ShadowClassifier().classify(semantic, current)
    comparison = ShadowComparisonBuilder().compare(classification, current, current)
    decision = EnforcedCoverageGate().enforce(comparison)

    assert decision.result == "BLOCK"
    assert decision.allow_model_invocation is False
    assert decision.context_mode == "F13_FULL_BLOCKED"
    with pytest.raises(RuntimeValidationError, match="CONTEXT_COVERAGE_BLOCKED"):
        EnforcedCoverageGate().invoke_with_fallback(
            "f13-context",
            lambda: comparison,
            lambda _context: "model-called",
        )


def test_c4_pass_keeps_f13_full_context_and_does_not_deliver_candidate() -> None:
    semantic = _semantic()
    mandatory = _unit("REQ-007", "mandatory", size=100)
    optional = _unit("OPTIONAL", "on_demand", size=500)
    current = [mandatory, optional]
    classification = ShadowClassifier().classify(semantic, current)
    comparison = ShadowComparisonBuilder().compare(classification, current, [mandatory])
    seen = []
    decision, result = EnforcedCoverageGate().invoke_with_fallback(
        "f13-full-context",
        lambda: comparison,
        lambda context: seen.append(context) or context,
    )

    assert decision.result == "ALLOW"
    assert decision.context_mode == "F13_FULL"
    assert seen == ["f13-full-context"]
    assert result == "f13-full-context"


def test_c4_optimizer_crash_falls_back_to_existing_f13_context() -> None:
    seen = []
    decision, result = EnforcedCoverageGate().invoke_with_fallback(
        {"sources": ["full-f13"]},
        lambda: (_ for _ in ()).throw(RuntimeError("classifier crash")),
        lambda context: seen.append(context) or "safe-model-call",
    )

    assert decision == EnforcementDecision(
        result="FALLBACK_F13",
        allow_model_invocation=True,
        context_mode="F13_FULL",
        reasons=("optimization_unavailable", "RuntimeError"),
        fallback_to_f13=True,
    )
    assert seen == [{"sources": ["full-f13"]}]
    assert result == "safe-model-call"

