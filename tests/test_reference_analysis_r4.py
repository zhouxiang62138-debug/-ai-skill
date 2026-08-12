"""R4 Reference-guided Design Exploration 定向测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.exploration import (
    DESIGN_PREVIEW_MODE_LEGACY,
    assign_reference_strategies,
    build_design_exploration_context,
    build_reference_integration_metadata,
    render_reference_integration_metadata,
    select_design_reference_decisions,
    validate_concept_reference_coverage,
    validate_preview_round,
)

from tests.test_exploration import REQUIRED_CONCEPTS, create_valid_round


def _synthesis(*, technical_only: bool = False, superseded: bool = False) -> dict:
    first_domain = "technical_architecture" if technical_only else "layout"
    value = {
        "synthesis_id": "REFSYN-001",
        "source_references": ["REF-001"],
        "decisions": {
            "adopt": [
                {
                    "decision_id": "REFDEC-001",
                    "domain": first_domain,
                    "source_findings": ["REFFND-001"],
                    "rationale": "test decision",
                    "decision_source": "reference_finding",
                    "user_scope_status": "include",
                }
            ],
            "adapt": [],
            "avoid": [],
        },
    }
    if superseded:
        value["status"] = "superseded"
    return value


def test_no_reference_keeps_context_without_reference_guidance() -> None:
    context = build_design_exploration_context(
        {"primary_goal": "test"}, "# Product Proposal"
    )
    assert context["active_reference_synthesis"] is None


def test_design_decision_selection_ignores_technical_only() -> None:
    selected = select_design_reference_decisions(_synthesis(technical_only=True))
    assert selected["design_relevant"] is False
    assert selected["decision_ids"] == []


def test_design_decision_selection_honors_exclusions() -> None:
    selected = select_design_reference_decisions(
        _synthesis(), source_metadata=[{"explicit_exclusions": ["layout"]}]
    )
    assert selected["decision_ids"] == []
    assert selected["excluded_decision_ids"] == ["REFDEC-001"]


def test_superseded_synthesis_is_rejected() -> None:
    with pytest.raises(Exception, match="superseded"):
        select_design_reference_decisions(_synthesis(superseded=True))


def test_strategy_assignment_is_deterministic_and_inspiration_has_no_faithful() -> None:
    first = assign_reference_strategies("inspiration", design_decisions=[{"decision_id": "REFDEC-001"}])
    second = assign_reference_strategies("inspiration", design_decisions=[{"decision_id": "REFDEC-001"}])
    assert first == second
    assert all(item["reference_strategy"] != "reference_faithful" for item in first)
    assert {item["strategy_variant"] for item in first} == {
        "strong_inspiration",
        "balanced_inspiration",
        "original_interpretation",
    }


def test_concept_metadata_contains_traceability_sections() -> None:
    metadata = build_reference_integration_metadata(_synthesis())
    rendered = render_reference_integration_metadata(metadata[0])
    assert "REFSYN-001" in rendered
    assert "REFDEC-001" in rendered
    for heading in (
        "Reference Strategy",
        "Referenced Decisions",
        "Adopted Decisions",
        "Adapted Decisions",
        "Not Used Decisions",
        "Explicit Exclusions",
        "Original Design Decisions",
    ):
        assert f"## {heading}" in rendered


def test_concept_reference_coverage_rejects_unknown_decision() -> None:
    metadata = build_reference_integration_metadata(_synthesis())[0]
    text = render_reference_integration_metadata(metadata).replace(
        "REFDEC-001", "REFDEC-999"
    )
    errors = validate_concept_reference_coverage(text, _synthesis())
    assert any("active synthesis" in error for error in errors)


def test_reference_guided_preview_round_preserves_existing_artifact_shape(tmp_path: Path) -> None:
    round_reference = create_valid_round(tmp_path)
    metadata = build_reference_integration_metadata(_synthesis())
    for concept, item in zip(REQUIRED_CONCEPTS, metadata):
        concept_dir = tmp_path / round_reference / concept
        concept_path = concept_dir / "concept.md"
        concept_path.write_text(
            concept_path.read_text(encoding="utf-8")
            + "\n"
            + render_reference_integration_metadata(item),
            encoding="utf-8",
        )
        html_path = concept_dir / "preview.html"
        html_path.write_text(
            html_path.read_text(encoding="utf-8").replace(
                "<body>",
                f'<body data-concept-id="{concept}" '
                f'data-reference-synthesis="REFSYN-001" '
                f'data-reference-strategy="{item["reference_strategy"]}">',
            ),
            encoding="utf-8",
        )
        (concept_dir / "preview.css").write_text(
            "--reference-strategy: " + item["reference_strategy"] + ";\n",
            encoding="utf-8",
        )
    assert validate_preview_round(
        tmp_path,
        round_reference,
        reference_synthesis=_synthesis(),
        preview_mode=DESIGN_PREVIEW_MODE_LEGACY,
    ) == []


def test_reference_context_only_contains_design_decisions() -> None:
    context = build_design_exploration_context(
        {"primary_goal": "test"},
        "# Product Proposal",
        active_reference_synthesis=_synthesis(),
    )
    reference_context = context["active_reference_synthesis"]
    assert reference_context["synthesis_id"] == "REFSYN-001"
    assert reference_context["design_relevant_decisions"][0]["decision_id"] == "REFDEC-001"
    assert "raw_html" not in str(reference_context)
