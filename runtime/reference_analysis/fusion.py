"""RA7-E Web 多证据融合。

融合只生成 Reference 证据摘要、冲突和限制项，不生成 PASS/FAIL，也不写入 Runtime 状态。
确定性 DOM/CSS/viewport 观察优先于视觉推断，但冲突双方都会保留。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from scripts.reference_protocol import assert_valid, validate_reference_finding, validate_reference_fusion

from .errors import ReferenceAnalysisError


_FUSION_ID = re.compile(r"^FUS-[0-9]{6}$")
_FINDING_ID = re.compile(r"^REFFND-[0-9]{3}$")
_EVIDENCE_ID = re.compile(r"^REFEV-[0-9]{3}$")
_RUN_ID = re.compile(r"^PER-[0-9]{6}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _value_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class DeterministicObservation:
    """来自 DOM/CSS/viewport 等可复测来源的观察。"""

    source_ref: str
    reference_id: str
    domain: str
    category: str
    value: Any
    evidence_refs: tuple[str, ...]
    measurement: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_ref, str) or not self.source_ref:
            raise ReferenceAnalysisError("FUSION_DETERMINISTIC_SOURCE_INVALID")
        if not re.fullmatch(r"REF-[0-9]{3}", self.reference_id):
            raise ReferenceAnalysisError("FUSION_REFERENCE_ID_INVALID")
        if not isinstance(self.domain, str) or not self.domain:
            raise ReferenceAnalysisError("FUSION_DOMAIN_INVALID")
        if not isinstance(self.category, str) or not self.category:
            raise ReferenceAnalysisError("FUSION_CATEGORY_INVALID")
        if not self.evidence_refs or not all(_EVIDENCE_ID.fullmatch(item) for item in self.evidence_refs):
            raise ReferenceAnalysisError("FUSION_EVIDENCE_REFS_INVALID")
        if self.measurement is not None and not isinstance(self.measurement, Mapping):
            raise ReferenceAnalysisError("FUSION_MEASUREMENT_INVALID")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, reference_id: str) -> "DeterministicObservation":
        if value.get("reference_id") != reference_id:
            raise ReferenceAnalysisError("FUSION_CROSS_REFERENCE_DENIED")
        evidence_refs = value.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            raise ReferenceAnalysisError("FUSION_EVIDENCE_REFS_INVALID")
        return cls(
            source_ref=str(value.get("source_ref") or value.get("finding_id") or ""),
            reference_id=reference_id,
            domain=str(value.get("domain") or ""),
            category=str(value.get("category") or ""),
            value=value.get("value", (value.get("observation") or {}).get("value") if isinstance(value.get("observation"), Mapping) else None),
            evidence_refs=tuple(str(item) for item in evidence_refs),
            measurement=value.get("measurement", (value.get("observation") or {}).get("measurement") if isinstance(value.get("observation"), Mapping) else None),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "reference_id": self.reference_id,
            "domain": self.domain,
            "category": self.category,
            "value": self.value,
            "evidence_refs": list(self.evidence_refs),
            "measurement": dict(self.measurement) if self.measurement is not None else None,
        }


@dataclass(frozen=True)
class FusionRequest:
    """一次只针对同一 Reference 的 deterministic/visual 融合请求。"""

    reference_id: str
    requested_domains: tuple[str, ...]
    deterministic_observations: tuple[DeterministicObservation, ...] = ()
    visual_findings: tuple[Mapping[str, Any], ...] = ()
    visual_run_id: str | None = None
    visual_run_status: str = "UNAVAILABLE"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"REF-[0-9]{3}", self.reference_id):
            raise ReferenceAnalysisError("FUSION_REFERENCE_ID_INVALID")
        if not self.requested_domains or not all(isinstance(item, str) and item for item in self.requested_domains):
            raise ReferenceAnalysisError("FUSION_DOMAINS_INVALID")
        if self.visual_run_id is not None and not _RUN_ID.fullmatch(self.visual_run_id):
            raise ReferenceAnalysisError("FUSION_PERCEPTION_RUN_ID_INVALID")
        if self.visual_run_status not in {"UNAVAILABLE", "SUCCEEDED", "FAILED", "BLOCKED"}:
            raise ReferenceAnalysisError("FUSION_PERCEPTION_STATUS_INVALID")
        if self.visual_findings and (self.visual_run_status != "SUCCEEDED" or self.visual_run_id is None):
            raise ReferenceAnalysisError("FUSION_VISUAL_PROVENANCE_INVALID")
        if any(item.reference_id != self.reference_id for item in self.deterministic_observations):
            raise ReferenceAnalysisError("FUSION_CROSS_REFERENCE_DENIED")
        for finding in self.visual_findings:
            errors = validate_reference_finding(finding)
            if errors:
                raise ReferenceAnalysisError("FUSION_VISUAL_FINDING_INVALID")
            if finding.get("reference_id") != self.reference_id:
                raise ReferenceAnalysisError("FUSION_CROSS_REFERENCE_DENIED")

    @property
    def input_hash(self) -> str:
        return _stable_hash(
            {
                "reference_id": self.reference_id,
                "requested_domains": self.requested_domains,
                "deterministic": [item.to_record() for item in self.deterministic_observations],
                "visual_findings": list(self.visual_findings),
                "visual_run_id": self.visual_run_id,
                "visual_run_status": self.visual_run_status,
            }
        )


@dataclass(frozen=True)
class FusionResult:
    fusion_id: str
    reference_id: str
    input_hash: str
    selected_source_refs: tuple[str, ...]
    visual_source_refs: tuple[str, ...]
    conflicts: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]
    binding_status: str
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not _FUSION_ID.fullmatch(self.fusion_id):
            raise ReferenceAnalysisError("FUSION_ID_INVALID")
        if not re.fullmatch(r"REF-[0-9]{3}", self.reference_id):
            raise ReferenceAnalysisError("FUSION_REFERENCE_ID_INVALID")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", self.input_hash):
            raise ReferenceAnalysisError("FUSION_INPUT_HASH_INVALID")
        if self.binding_status not in {"READY", "REVIEW_REQUIRED", "BLOCKED"}:
            raise ReferenceAnalysisError("FUSION_BINDING_STATUS_INVALID")

    def to_record(self) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "fusion_id": self.fusion_id,
            "reference_id": self.reference_id,
            "input_hash": self.input_hash,
            "selected_source_refs": list(self.selected_source_refs),
            "visual_source_refs": list(self.visual_source_refs),
            "conflicts": [dict(item) for item in self.conflicts],
            "limitations": list(self.limitations),
            "binding_status": self.binding_status,
            "created_at": self.created_at,
            "trust_level": "untrusted",
        }
        assert_valid(validate_reference_fusion(record), "reference_fusion")
        return record


class ReferenceFusionEngine:
    """按 deterministic 优先、冲突保留的规则生成融合摘要。"""

    def __init__(self, *, max_conflicts: int = 128) -> None:
        if not isinstance(max_conflicts, int) or max_conflicts < 1:
            raise ReferenceAnalysisError("FUSION_LIMIT_INVALID")
        self.max_conflicts = max_conflicts

    def fuse(self, request: FusionRequest, *, fusion_id: str = "FUS-000001") -> FusionResult:
        deterministic_by_key: dict[tuple[str, str], list[DeterministicObservation]] = {}
        for item in request.deterministic_observations:
            deterministic_by_key.setdefault((item.domain, item.category), []).append(item)
        visual_by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for item in request.visual_findings:
            key = (str(item["domain"]), str(item["category"]))
            visual_by_key.setdefault(key, []).append(item)

        selected: list[str] = []
        visual_refs: list[str] = []
        conflicts: list[dict[str, Any]] = []
        for key, observations in deterministic_by_key.items():
            selected.extend(item.source_ref for item in observations)
            visuals = visual_by_key.pop(key, [])
            visual_refs.extend(str(item["finding_id"]) for item in visuals)
            deterministic_values = {_value_key(item.value) for item in observations}
            visual_values = {
                _value_key((item.get("observation") or {}).get("value") if isinstance(item.get("observation"), Mapping) else None)
                for item in visuals
            }
            if visuals and deterministic_values.isdisjoint(visual_values):
                if len(conflicts) >= self.max_conflicts:
                    raise ReferenceAnalysisError("FUSION_CONFLICT_LIMIT_EXCEEDED")
                conflicts.append(
                    {
                        "conflict_id": f"REFCON-{len(conflicts) + 1:03d}",
                        "domain": key[0],
                        "category": key[1],
                        "deterministic_refs": [item.source_ref for item in observations],
                        "visual_refs": [str(item["finding_id"]) for item in visuals],
                        "resolution": "deterministic_precedence_preserve_visual",
                        "review_required": True,
                    }
                )
        for key, visuals in visual_by_key.items():
            visual_refs.extend(str(item["finding_id"]) for item in visuals)
            if key[0] in request.requested_domains:
                selected.extend(str(item["finding_id"]) for item in visuals)

        limitations: list[str] = []
        if request.visual_run_status != "SUCCEEDED":
            limitations.append(f"VISUAL_PERCEPTION_{request.visual_run_status}")
        if conflicts:
            limitations.append("DETERMINISTIC_VISUAL_CONFLICT_REVIEW_REQUIRED")
        missing_domains = [
            domain
            for domain in request.requested_domains
            if not any(item.domain == domain for item in request.deterministic_observations)
            and not any(str(item.get("domain")) == domain for item in request.visual_findings)
        ]
        if missing_domains:
            limitations.append("MISSING_DOMAINS:" + ",".join(missing_domains))
        binding_status = "REVIEW_REQUIRED" if conflicts else "READY"
        if missing_domains or request.visual_run_status in {"UNAVAILABLE", "FAILED", "BLOCKED"}:
            binding_status = "BLOCKED"
        return FusionResult(
            fusion_id=fusion_id,
            reference_id=request.reference_id,
            input_hash=request.input_hash,
            selected_source_refs=tuple(dict.fromkeys(selected)),
            visual_source_refs=tuple(dict.fromkeys(visual_refs)),
            conflicts=tuple(conflicts),
            limitations=tuple(limitations),
            binding_status=binding_status,
        )
