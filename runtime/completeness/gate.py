"""Feature Completeness 分数与 Gate 计算。"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .models import FeatureFinding, FeatureObservation


def _profile_data(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    value = profile.get("feature_completeness", profile)
    if not isinstance(value, Mapping):
        raise ValueError("feature_completeness Profile 无效")
    return value


def evaluate_feature_completeness(
    profile: Mapping[str, Any],
    findings: Sequence[FeatureFinding],
    observations: Sequence[FeatureObservation],
    *,
    static_scan_completed: bool = True,
) -> dict[str, Any]:
    """综合静态、Runtime、Browser 和 Requirement/AC 证据计算 Gate 输入。"""

    data = _profile_data(profile)
    required = data.get("required", False)
    if not isinstance(required, bool):
        raise ValueError("feature_completeness.required 无效")
    if not required:
        return {
            "result": "SKIPPED",
            "score": None,
            "minimum_score": None,
            "reason": "feature_completeness_not_required",
            "finding_refs": [],
            "observation_refs": [],
            "evidence_refs": [],
        }
    minimum = data.get("minimum_score", 8.0)
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not 0 <= minimum <= 10:
        raise ValueError("feature_completeness.minimum_score 无效")
    required_sources = data.get("required_sources", ["static", "runtime"])
    if not isinstance(required_sources, list) or not all(
        isinstance(item, str) for item in required_sources
    ):
        raise ValueError("feature_completeness.required_sources 无效")
    score = max(0.0, min(10.0, 10.0 - sum(item.penalty for item in findings)))
    finding_refs = [item.finding_id for item in findings]
    observation_refs = [item.observation_id for item in observations]
    sources = {item.source for item in observations}
    if static_scan_completed:
        sources.add("static")
    missing_sources = [item for item in required_sources if item not in sources]
    critical = [
        item
        for item in findings
        if item.severity in {"blocker", "critical"}
    ]
    failed = [item for item in observations if item.result == "FAIL"]
    blocked = [item for item in observations if item.result == "BLOCKED"]
    missing_links = [
        item
        for item in observations
        if not item.requirement_id or not item.acceptance_criterion_id
    ]
    if missing_sources:
        result = "FAIL"
        reason = "required_feature_evidence_missing:" + ",".join(missing_sources)
    elif missing_links:
        result = "FAIL"
        reason = "feature_observation_traceability_missing"
    elif failed or critical:
        result = "FAIL"
        reason = "incomplete_or_stub_implementation"
    elif blocked:
        result = "BLOCKED"
        reason = "feature_evaluation_environment_blocked"
    elif score < float(minimum):
        result = "FAIL"
        reason = "feature_completeness_below_minimum"
    else:
        result = "PASS"
        reason = None
    return {
        "result": result,
        "score": round(score, 3),
        "minimum_score": float(minimum),
        "reason": reason,
        "finding_refs": finding_refs,
        "observation_refs": observation_refs,
        "evidence_refs": finding_refs + observation_refs,
        "static_scan_completed": static_scan_completed,
        "required_sources": list(required_sources),
    }
