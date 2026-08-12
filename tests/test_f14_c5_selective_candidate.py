"""F14-C5 候选 Selective Context 的只评估不交付测试。"""

from __future__ import annotations

from runtime.context import (
    SelectiveContextEvaluator,
    ShadowClassifier,
    ShadowComparisonBuilder,
)
from tests.test_f14_c_shadow_coverage import _semantic, _unit


def test_c5_eligible_candidate_is_only_an_evaluation_artifact() -> None:
    semantic = _semantic()
    mandatory = _unit("REQ-007", "mandatory", size=100)
    task = _unit("AC-014", "task_relevant", size=300)
    current = [mandatory, task]
    candidate = [mandatory]
    classification = ShadowClassifier().classify(semantic, current)
    comparison = ShadowComparisonBuilder().compare(classification, current, candidate)
    evaluation = SelectiveContextEvaluator().evaluate(
        comparison,
        candidate,
        candidate_id="candidate-001",
    )

    assert evaluation.eligible is True
    assert evaluation.mandatory_coverage == "COMPLETE"
    assert evaluation.unknown_count == 0
    assert evaluation.authority_verified is True
    assert evaluation.reduction_bytes == 300
    assert evaluation.fallback_to_f13 is False


def test_c5_unknown_authority_forces_safe_fallback() -> None:
    semantic = _semantic()
    mandatory = _unit("REQ-007", "mandatory")
    unknown = _unit("UNKNOWN-001", "task_relevant", authority="UNKNOWN")
    current = [mandatory, unknown]
    classification = ShadowClassifier().classify(semantic, current)
    comparison = ShadowComparisonBuilder().compare(classification, current, [mandatory])
    evaluation = SelectiveContextEvaluator().evaluate(
        comparison,
        [mandatory, unknown],
        candidate_id="candidate-unknown",
    )

    assert evaluation.eligible is False
    assert evaluation.fallback_to_f13 is True
    assert "unknown_dependency_or_authority" in evaluation.reasons
    assert "authority_unverified" in evaluation.reasons

