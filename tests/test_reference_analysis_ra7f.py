"""RA7-F Visual Conformance Provider 契约测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from runtime.reference_analysis.errors import ReferenceAnalysisError
from runtime.reference_analysis.perception import CodexNativeMultimodalPerceptionProvider, ImageEvidenceInput
from runtime.reference_analysis.visual_conformance import VisualComparisonRequest, VisualConformanceProvider


def _png(seed: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + seed + b"\x00" * 32


def _evidence(raw: bytes, evidence_id: str, artifact_ref: str) -> ImageEvidenceInput:
    return ImageEvidenceInput(
        evidence_id=evidence_id,
        reference_id="REF-001",
        artifact_ref=artifact_ref,
        sha256=hashlib.sha256(raw).hexdigest(),
        mime_type="image/png",
    )


def _request(reference: ImageEvidenceInput, implementation: ImageEvidenceInput) -> VisualComparisonRequest:
    return VisualComparisonRequest(
        reference_id="REF-001",
        approved_binding_ref="REFDEC-001",
        reference_image=reference,
        implementation_image=implementation,
    )


def test_visual_conformance_without_native_perception_is_blocked() -> None:
    raw_a = _png(b"a")
    raw_b = _png(b"b")
    request = _request(
        _evidence(raw_a, "REFEV-001", "artifacts/references/reference.png"),
        _evidence(raw_b, "REFEV-002", "artifacts/references/implementation.png"),
    )
    run, findings = VisualConformanceProvider().compare(request, root=Path.cwd())
    assert run.status == "BLOCKED"
    assert run.comparison_status == "BLOCKED"
    assert findings == ()
    assert "VISUAL_PERCEPTION_UNAVAILABLE" in run.limitations


def test_visual_conformance_returns_unverified_structured_findings(tmp_path: Path) -> None:
    ref = tmp_path / "artifacts" / "references" / "reference.png"
    impl = tmp_path / "artifacts" / "references" / "implementation.png"
    ref.parent.mkdir(parents=True)
    raw_a = _png(b"a")
    raw_b = _png(b"b")
    ref.write_bytes(raw_a)
    impl.write_bytes(raw_b)

    def bridge(_payload: dict[str, object]) -> dict[str, object]:
        return {
            "findings": [
                {
                    "schema_version": 1,
                    "finding_id": "REFFND-001",
                    "reference_id": "REF-001",
                    "domain": "visual_style",
                    "category": "color_difference",
                    "observation": {"value": "implementation uses a darker accent", "measurement": None, "notes": None},
                    "epistemic_status": "inferred",
                    "confidence": "medium",
                    "evidence_refs": ["REFEV-001", "REFEV-002"],
                    "user_scope_status": "unspecified",
                    "inference_basis": ["comparison of two screenshots"],
                    "unknown_reason": None,
                    "supersedes": None,
                    "trust_level": "untrusted",
                    "created_at": "2026-08-10T00:00:00Z",
                }
            ],
            "limitations": ["visual estimate only"],
        }

    provider = VisualConformanceProvider(CodexNativeMultimodalPerceptionProvider(invocation_adapter=bridge))
    run, findings = provider.compare(
        _request(
            _evidence(raw_a, "REFEV-001", "artifacts/references/reference.png"),
            _evidence(raw_b, "REFEV-002", "artifacts/references/implementation.png"),
        ),
        root=tmp_path,
    )
    assert run.status == "SUCCEEDED"
    assert run.comparison_status == "UNVERIFIED"
    assert run.result_hash
    assert findings[0]["evidence_refs"] == ["REFEV-001", "REFEV-002"]
    assert run.to_record()["comparison_status"] == "UNVERIFIED"


def test_visual_conformance_requires_approved_binding_and_two_distinct_images() -> None:
    raw = _png(b"a")
    reference = _evidence(raw, "REFEV-001", "artifacts/references/reference.png")
    with pytest.raises(ReferenceAnalysisError) as exc:
        VisualComparisonRequest(
            reference_id="REF-001",
            approved_binding_ref="not-approved",
            reference_image=reference,
            implementation_image=_evidence(raw, "REFEV-002", "artifacts/references/implementation.png"),
        )
    assert exc.value.code == "VISUAL_APPROVED_BINDING_REQUIRED"

    with pytest.raises(ReferenceAnalysisError) as exc:
        VisualComparisonRequest(
            reference_id="REF-001",
            approved_binding_ref="REFDEC-001",
            reference_image=reference,
            implementation_image=reference,
        )
    assert exc.value.code == "VISUAL_TWO_SCREENSHOTS_REQUIRED"
