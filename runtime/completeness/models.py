"""Feature Completeness 的结构化结果模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_FINDING_ID = re.compile(r"^FC-FIND-[0-9]{3}$")
_OBSERVATION_ID = re.compile(r"^FC-OBS-[0-9]{3}$")


@dataclass(frozen=True)
class FeatureFinding:
    """静态代码扫描发现。"""

    finding_id: str
    category: str
    severity: str
    path: str
    line: int
    evidence: str
    penalty: float

    def __post_init__(self) -> None:
        if not _FINDING_ID.fullmatch(self.finding_id):
            raise ValueError("finding_id 格式无效")
        if self.severity not in {"blocker", "critical", "major", "minor", "observation"}:
            raise ValueError("finding severity 无效")
        if not self.path or not isinstance(self.line, int) or self.line <= 0:
            raise ValueError("finding path/line 无效")

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "evidence": self.evidence,
            "penalty": self.penalty,
        }


@dataclass(frozen=True)
class FeatureObservation:
    """来自 Runtime、Browser 或验收流程的行为观察。"""

    observation_id: str
    source: str
    requirement_id: str
    acceptance_criterion_id: str
    expected: str
    observed: str
    result: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _OBSERVATION_ID.fullmatch(self.observation_id):
            raise ValueError("observation_id 格式无效")
        if self.source not in {"runtime", "browser", "requirement"}:
            raise ValueError("observation source 无效")
        if not self.requirement_id or not self.acceptance_criterion_id:
            raise ValueError("observation 必须关联 Requirement/AC")
        if self.result not in {"PASS", "FAIL", "BLOCKED"}:
            raise ValueError("observation result 无效")
        if not self.evidence_refs or not all(
            isinstance(item, str) and item for item in self.evidence_refs
        ):
            raise ValueError("observation 必须包含 Evidence 引用")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "source": self.source,
            "requirement_id": self.requirement_id,
            "acceptance_criterion_id": self.acceptance_criterion_id,
            "expected": self.expected,
            "observed": self.observed,
            "result": self.result,
            "evidence_refs": list(self.evidence_refs),
        }
