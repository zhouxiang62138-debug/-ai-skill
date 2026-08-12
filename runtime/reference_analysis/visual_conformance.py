"""RA7-F 视觉一致性 Provider。

该 Provider 只消费已经批准的绑定和两份已哈希截图，输出结构化比较 Finding。
它不拥有 R6 Gate 决策权，不会自行生成 PASS/FAIL 或修改验收阈值。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.reference_protocol import assert_valid, validate_visual_conformance

from .acquisition import ProviderAvailability
from .errors import ReferenceAnalysisError
from .perception import (
    CodexNativeMultimodalPerceptionProvider,
    ImageEvidenceInput,
    PerceptionRequest,
)


_RUN_ID = re.compile(r"^VC-[0-9]{6}$")
_DECISION_ID = re.compile(r"^REFDEC-[0-9]{3,4}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class VisualComparisonRequest:
    reference_id: str
    approved_binding_ref: str
    reference_image: ImageEvidenceInput
    implementation_image: ImageEvidenceInput
    requested_domains: tuple[str, ...] = ("layout", "visual_style", "components", "design_tokens")

    def __post_init__(self) -> None:
        if not re.fullmatch(r"REF-[0-9]{3}", self.reference_id):
            raise ReferenceAnalysisError("VISUAL_REFERENCE_ID_INVALID")
        if not _DECISION_ID.fullmatch(self.approved_binding_ref):
            raise ReferenceAnalysisError("VISUAL_APPROVED_BINDING_REQUIRED")
        if self.reference_image.reference_id != self.reference_id or self.implementation_image.reference_id != self.reference_id:
            raise ReferenceAnalysisError("VISUAL_CROSS_REFERENCE_DENIED")
        if self.reference_image.evidence_id == self.implementation_image.evidence_id:
            raise ReferenceAnalysisError("VISUAL_TWO_SCREENSHOTS_REQUIRED")
        if self.reference_image.artifact_ref == self.implementation_image.artifact_ref:
            raise ReferenceAnalysisError("VISUAL_DISTINCT_SCREENSHOTS_REQUIRED")
        if not self.requested_domains or not all(isinstance(item, str) and item for item in self.requested_domains):
            raise ReferenceAnalysisError("VISUAL_DOMAINS_INVALID")

    @property
    def input_hash(self) -> str:
        return _hash(
            {
                "reference_id": self.reference_id,
                "approved_binding_ref": self.approved_binding_ref,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "artifact_ref": item.artifact_ref,
                        "sha256": item.sha256,
                        "mime_type": item.mime_type,
                    }
                    for item in (self.reference_image, self.implementation_image)
                ],
                "requested_domains": self.requested_domains,
            }
        )


@dataclass(frozen=True)
class VisualConformanceRun:
    run_id: str
    reference_id: str
    approved_binding_ref: str
    input_evidence_hashes: tuple[str, ...]
    status: str
    comparison_status: str
    perception_run_id: str | None = None
    result_hash: str | None = None
    limitations: tuple[str, ...] = ()
    created_at: str = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not _RUN_ID.fullmatch(self.run_id):
            raise ReferenceAnalysisError("VISUAL_RUN_ID_INVALID")
        if not re.fullmatch(r"REF-[0-9]{3}", self.reference_id):
            raise ReferenceAnalysisError("VISUAL_REFERENCE_ID_INVALID")
        if not _DECISION_ID.fullmatch(self.approved_binding_ref):
            raise ReferenceAnalysisError("VISUAL_APPROVED_BINDING_REQUIRED")
        if self.status not in {"BLOCKED", "SUCCEEDED"}:
            raise ReferenceAnalysisError("VISUAL_RUN_STATUS_INVALID")
        if self.comparison_status not in {"BLOCKED", "UNVERIFIED"}:
            raise ReferenceAnalysisError("VISUAL_COMPARISON_STATUS_INVALID")
        if self.status == "SUCCEEDED" and self.comparison_status != "UNVERIFIED":
            raise ReferenceAnalysisError("VISUAL_GATE_DECISION_FORBIDDEN")
        if self.perception_run_id is not None and not re.fullmatch(r"PER-[0-9]{6}", self.perception_run_id):
            raise ReferenceAnalysisError("VISUAL_PERCEPTION_RUN_ID_INVALID")

    def to_record(self) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "run_id": self.run_id,
            "reference_id": self.reference_id,
            "approved_binding_ref": self.approved_binding_ref,
            "input_evidence_hashes": list(self.input_evidence_hashes),
            "status": self.status,
            "comparison_status": self.comparison_status,
            "perception_run_id": self.perception_run_id,
            "result_hash": self.result_hash,
            "limitations": list(self.limitations),
            "created_at": self.created_at,
            "trust_level": "untrusted",
        }
        assert_valid(validate_visual_conformance(record), "visual_conformance")
        return record


class VisualConformanceProvider:
    """将两张截图交给受控感知 Provider，永不越权决定 R6 Gate。"""

    provider_id = "reference-visual-conformance"
    version = 1

    def __init__(self, perception_provider: CodexNativeMultimodalPerceptionProvider | None = None) -> None:
        self.perception_provider = perception_provider or CodexNativeMultimodalPerceptionProvider()

    @property
    def availability(self) -> ProviderAvailability:
        return self.perception_provider.availability

    def compare(
        self,
        request: VisualComparisonRequest,
        *,
        root: str | Path,
        path_policy: ExecutionPathPolicy | None = None,
        run_id: str = "VC-000001",
        perception_run_id: str = "PER-000001",
    ) -> tuple[VisualConformanceRun, tuple[dict[str, Any], ...]]:
        if not _RUN_ID.fullmatch(run_id):
            raise ReferenceAnalysisError("VISUAL_RUN_ID_INVALID")
        if self.availability is not ProviderAvailability.AVAILABLE:
            run = VisualConformanceRun(
                run_id=run_id,
                reference_id=request.reference_id,
                approved_binding_ref=request.approved_binding_ref,
                input_evidence_hashes=(request.reference_image.sha256, request.implementation_image.sha256),
                status="BLOCKED",
                comparison_status="BLOCKED",
                limitations=("VISUAL_PERCEPTION_UNAVAILABLE",),
            )
            return run, ()
        perception_request = PerceptionRequest(
            reference_id=request.reference_id,
            evidence=(request.reference_image, request.implementation_image),
            requested_domains=request.requested_domains,
            scope="approved reference screenshot versus implementation screenshot",
            explicit_exclusions=("exact_pixel_pass_fail", "automatic_acceptance_decision"),
        )
        try:
            perception_run, findings = self.perception_provider.perceive(
                perception_request,
                root=root,
                path_policy=path_policy,
                run_id=perception_run_id,
            )
        except ReferenceAnalysisError as exc:
            run = VisualConformanceRun(
                run_id=run_id,
                reference_id=request.reference_id,
                approved_binding_ref=request.approved_binding_ref,
                input_evidence_hashes=(request.reference_image.sha256, request.implementation_image.sha256),
                status="BLOCKED",
                comparison_status="BLOCKED",
                limitations=("VISUAL_PERCEPTION_UNAVAILABLE", exc.code),
            )
            return run, ()
        if perception_run.status != "SUCCEEDED":
            run = VisualConformanceRun(
                run_id=run_id,
                reference_id=request.reference_id,
                approved_binding_ref=request.approved_binding_ref,
                input_evidence_hashes=(request.reference_image.sha256, request.implementation_image.sha256),
                status="BLOCKED",
                comparison_status="BLOCKED",
                perception_run_id=perception_run.run_id,
                limitations=perception_run.limitations,
            )
            return run, ()
        run = VisualConformanceRun(
            run_id=run_id,
            reference_id=request.reference_id,
            approved_binding_ref=request.approved_binding_ref,
            input_evidence_hashes=(request.reference_image.sha256, request.implementation_image.sha256),
            status="SUCCEEDED",
            comparison_status="UNVERIFIED",
            perception_run_id=perception_run.run_id,
            result_hash=_hash(list(findings)),
            limitations=("VISUAL_FINDINGS_REQUIRE_R6_DETERMINISTIC_EVIDENCE",),
        )
        return run, findings
