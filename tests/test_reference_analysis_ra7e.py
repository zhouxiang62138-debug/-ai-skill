"""RA7-E 多证据融合契约测试。"""

from __future__ import annotations

import pytest

from runtime.reference_analysis.errors import ReferenceAnalysisError
from runtime.reference_analysis.fusion import (
    DeterministicObservation,
    FusionRequest,
    ReferenceFusionEngine,
)


def _visual_finding(value: str, *, finding_id: str = "REFFND-001") -> dict[str, object]:
    return {
        "schema_version": 1,
        "finding_id": finding_id,
        "reference_id": "REF-001",
        "domain": "layout",
        "category": "navigation_structure",
        "observation": {"value": value, "measurement": None, "notes": None},
        "epistemic_status": "inferred",
        "confidence": "medium",
        "evidence_refs": ["REFEV-001"],
        "user_scope_status": "unspecified",
        "inference_basis": ["visual appearance"],
        "unknown_reason": None,
        "supersedes": None,
        "trust_level": "untrusted",
        "created_at": "2026-08-10T00:00:00Z",
    }


def _deterministic(value: str, *, source_ref: str = "DOM-001") -> DeterministicObservation:
    return DeterministicObservation(
        source_ref=source_ref,
        reference_id="REF-001",
        domain="layout",
        category="navigation_structure",
        value=value,
        evidence_refs=("REFEV-001",),
    )


def test_deterministic_observation_wins_but_visual_conflict_is_preserved() -> None:
    request = FusionRequest(
        reference_id="REF-001",
        requested_domains=("layout",),
        deterministic_observations=(_deterministic("sidebar"),),
        visual_findings=(_visual_finding("top navigation"),),
        visual_run_id="PER-000001",
        visual_run_status="SUCCEEDED",
    )
    result = ReferenceFusionEngine().fuse(request)
    record = result.to_record()
    assert result.binding_status == "REVIEW_REQUIRED"
    assert result.selected_source_refs == ("DOM-001",)
    assert result.visual_source_refs == ("REFFND-001",)
    assert record["conflicts"][0]["resolution"] == "deterministic_precedence_preserve_visual"
    assert record["trust_level"] == "untrusted"


def test_unavailable_visual_capability_blocks_missing_domain_without_fake_finding() -> None:
    request = FusionRequest(
        reference_id="REF-001",
        requested_domains=("visual_style",),
        visual_run_status="UNAVAILABLE",
    )
    result = ReferenceFusionEngine().fuse(request, fusion_id="FUS-000002")
    assert result.binding_status == "BLOCKED"
    assert "VISUAL_PERCEPTION_UNAVAILABLE" in result.limitations
    assert "MISSING_DOMAINS:visual_style" in result.limitations
    assert result.selected_source_refs == ()
    assert result.visual_source_refs == ()


def test_visual_findings_require_successful_perception_provenance() -> None:
    with pytest.raises(ReferenceAnalysisError) as exc:
        FusionRequest(
            reference_id="REF-001",
            requested_domains=("layout",),
            visual_findings=(_visual_finding("sidebar"),),
            visual_run_id="PER-000003",
            visual_run_status="BLOCKED",
        )
    assert exc.value.code == "FUSION_VISUAL_PROVENANCE_INVALID"


def test_cross_reference_deterministic_observation_is_rejected() -> None:
    with pytest.raises(ReferenceAnalysisError) as exc:
        FusionRequest(
            reference_id="REF-001",
            requested_domains=("layout",),
            deterministic_observations=(
                DeterministicObservation(
                    source_ref="DOM-002",
                    reference_id="REF-002",
                    domain="layout",
                    category="navigation_structure",
                    value="sidebar",
                    evidence_refs=("REFEV-001",),
                ),
            ),
        )
    assert exc.value.code == "FUSION_CROSS_REFERENCE_DENIED"
