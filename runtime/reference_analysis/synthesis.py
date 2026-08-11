"""多来源 Reference Findings 的确定性合成引擎。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReferenceSynthesisEngine:
    """只消费已验证 Finding，不生成产品需求、计划或 Agent 指令。"""

    def __init__(self, *, config: Mapping[str, Any]) -> None:
        self.config = config

    def synthesize(
        self,
        sources: list[Mapping[str, Any]],
        scopes: Mapping[str, Mapping[str, str]],
        findings: list[Mapping[str, Any]],
        *,
        context: Mapping[str, Any],
        synthesis_id: str,
        run_fingerprint: str,
    ) -> dict[str, Any]:
        by_domain: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for finding in findings:
            by_domain[str(finding["domain"])].append(finding)
        decisions: dict[str, list[dict[str, Any]]] = {"adopt": [], "adapt": [], "avoid": []}
        unknown: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        decision_number = 1
        conflict_number = 1
        for domain in self.config.get("analysis_domains", []):
            domain_findings = by_domain.get(domain, [])
            if not domain_findings:
                continue
            grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
            for finding in domain_findings:
                grouped[str(finding["reference_id"])].append(finding)
            observed = [item for item in domain_findings if item.get("epistemic_status") in {"observed", "inferred"}]
            observed_values = {str(item.get("observation", {}).get("value")) for item in observed}
            if len(grouped) > 1 and len(observed_values) > 1:
                refs = [str(item["finding_id"]) for item in observed]
                conflicts.append(
                    {
                        "conflict_id": f"REFCON-{conflict_number:03d}",
                        "domain": domain,
                        "source_findings": refs[: max(2, len(refs))],
                        "statement": "多个来源对同一领域给出了不一致观察，不能静默选择。",
                        "resolution_status": "requires_planner_resolution",
                    }
                )
                conflict_number += 1
            for reference_id, items in grouped.items():
                scope = scopes.get(reference_id, {})
                scope_state = str(scope.get(domain, "unspecified"))
                ids = [str(item["finding_id"]) for item in items]
                if scope_state == "exclude":
                    decisions["avoid"].append(
                        {
                            "decision_id": f"REFDEC-{decision_number:03d}",
                            "domain": domain,
                            "source_findings": ids,
                            "rationale": "用户明确排除此领域，引用仅保留为 avoid 约束。",
                            "decision_source": "user_explicit_exclusion",
                            "user_scope_status": "exclude",
                        }
                    )
                    decision_number += 1
                    continue
                usable = [item for item in items if item.get("epistemic_status") in {"observed", "inferred"}]
                unknown_items = [item for item in items if item.get("epistemic_status") == "unknown"]
                if usable:
                    bucket = "adapt" if any(
                        source.get("reference_id") == reference_id and source.get("reference_mode") == "adaptation"
                        for source in sources
                    ) else "adopt"
                    decisions[bucket].append(
                        {
                            "decision_id": f"REFDEC-{decision_number:03d}",
                            "domain": domain,
                            "source_findings": [str(item["finding_id"]) for item in usable],
                            "rationale": "基于可追溯的 observed/inferred Finding 形成候选参考决策。",
                            "decision_source": "reference_finding",
                            "user_scope_status": scope_state,
                        }
                    )
                    decision_number += 1
                for item in unknown_items:
                    unknown.append(
                        {
                            "domain": domain,
                            "statement": str(item.get("observation", {}).get("value", "未知")),
                            "source_findings": [str(item["finding_id"])],
                            "reason": str(item.get("unknown_reason") or "证据不足或能力未启用"),
                        }
                    )
        return {
            "schema_version": 1,
            "synthesis_id": synthesis_id,
            "source_references": [str(source["reference_id"]) for source in sources],
            "decisions": decisions,
            "unknown": unknown,
            "conflicts": conflicts,
            "priority_policy": {
                "explicit_user_requirements_override_reference": True,
                "approved_product_artifacts_override_reference": True,
                "reference_decision_is_not_requirement": True,
            },
            "created_at": _now(),
            "context": dict(context),
            "trust_level": "untrusted",
            "supersedes": None,
            "run_fingerprint": run_fingerprint,
        }
