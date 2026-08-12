"""研究证据到创意机会的受控转换。

这里允许发散，但每个候选必须保留证据引用，并且永远停留在候选状态，
由 Planner 和用户按目标、范围与风险重新治理。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


_MODES = {"PATTERN", "OBSERVATION", "OPPORTUNITY", "IDEA"}
_PROFILES = {"conservative", "balanced", "innovative"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_opportunity_map(
    findings: Iterable[Mapping[str, Any]],
    *,
    project_id: str,
    opportunity_id: str = "OPP-001",
    creativity_profile: str = "balanced",
    created_at: str | None = None,
    supersedes: str | None = None,
) -> dict[str, Any]:
    """从研究发现生成候选机会，不生成 Must Have，也不改变需求状态。"""

    if creativity_profile not in _PROFILES:
        raise ValueError("creativity_profile 必须是 conservative、balanced 或 innovative")
    candidates: list[dict[str, Any]] = []
    for index, finding in enumerate(findings, start=1):
        if not isinstance(finding, Mapping):
            continue
        mode = str(finding.get("epistemic_status") or "").upper()
        claim = str(finding.get("claim") or "").strip()
        if mode not in _MODES or not claim:
            continue
        finding_id = str(finding.get("finding_id") or f"finding-{index:03d}")
        title_prefix = {
            "PATTERN": "可借鉴模式",
            "OBSERVATION": "可验证观察",
            "OPPORTUNITY": "机会候选",
            "IDEA": "创意候选",
        }[mode]
        candidates.append(
            {
                "candidate_id": f"{opportunity_id}-C{index:03d}",
                "title": f"{title_prefix}：{claim[:48]}",
                "description": claim,
                "mode": mode,
                "evidence_refs": [finding_id],
                "decision_status": "candidate",
                "requires_user_decision": True,
            }
        )
    return {
        "schema_version": 1,
        "opportunity_id": opportunity_id,
        "project_id": project_id,
        "creativity_profile": creativity_profile,
        "candidates": candidates,
        "governance_policy": {
            "never_auto_promote_to_requirement": True,
            "user_goal_check_required": True,
            "scope_impact_check_required": True,
        },
        "created_at": created_at or _now(),
        "supersedes": supersedes,
    }
