"""能力分析器：对没有语义适配器的来源明确输出 unknown。"""

from __future__ import annotations

from typing import Any, Mapping

from ..models import AnalyzerResult, FindingDraft, NormalizedReference


class CapabilityAnalyzer:
    def __init__(self, *, config: Mapping[str, Any], version: int = 1) -> None:
        self.config = config
        self.version = version

    def supports(self, normalized: NormalizedReference) -> bool:
        return False

    def analyze(
        self,
        normalized: NormalizedReference,
        *,
        scope: Mapping[str, str],
        evidence_refs: tuple[str, ...],
    ) -> AnalyzerResult:
        findings: list[FindingDraft] = []
        reason = "; ".join(normalized.limitations) or "SEMANTIC_ANALYZER_UNAVAILABLE"
        for domain in self.config.get("analysis_domains", []):
            state = scope.get(domain, "unspecified")
            if state == "exclude":
                continue
            findings.append(
                FindingDraft(
                    domain=domain,
                    category="capability_unavailable",
                    value=f"{normalized.source_type} 当前不能验证 {domain}",
                    epistemic_status="unknown",
                    confidence="low",
                    evidence_refs=evidence_refs,
                    user_scope_status=state,
                    unknown_reason=reason,
                )
            )
        return AnalyzerResult(tuple(findings), supported=False, limitation=reason)
