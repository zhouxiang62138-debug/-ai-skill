"""两阶段 Design Exploration 的回归测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from exploration import (  # noqa: E402
    DESIGN_PREVIEW_MODE_COMPARISON,
    DESIGN_PREVIEW_MODE_SELECTED,
    DIRECTION_REQUIRED_SECTIONS,
    REQUIRED_CONCEPTS,
    REQUIRED_SECTIONS,
    begin_exploration,
    decide_design_exploration,
    finalize_preview_round,
    validate_preview_round,
)
from feedback import (  # noqa: E402
    apply_feedback_decision,
    classify_feedback,
    integrate_feedback_into_proposal,
)
from project_state import ProjectStateError  # noqa: E402


ROUND_001 = "artifacts/design_previews/round_001"
ROUND_002 = "artifacts/design_previews/round_002"


def base_state() -> dict:
    return {
        "schema_version": 4,
        "project_id": "test_two_stage_design",
        "status": "PLANNING",
        "current_iteration": 0,
        "next_role": "planner",
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "requirements_version": 1,
        "active_requirements": "memory/requirements/requirements_v001.yaml",
        "proposal_status": "draft",
        "proposal_version": 1,
        "active_proposal": "memory/proposals/product_proposal_v001.md",
        "approved_proposal": None,
        "user_approval_status": "not_requested",
        "product_spec_status": "not_started",
        "plan_status": "not_started",
        "active_plan": None,
        "plan_approval_status": "not_requested",
        "design_exploration_required": None,
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
        "selected_design_concept": None,
        "design_selection_record": None,
        "exploration_feedback_record": None,
    }


def exploration_decision():
    return decide_design_exploration(
        {
            "design_preferences": {
                "status": "undecided",
                "routing": "design_exploration",
                "specification_completeness": "none",
            }
        }
    )


def write_comparison_round(root: Path) -> None:
    round_dir = root / ROUND_001
    for index, concept_name in enumerate(REQUIRED_CONCEPTS, 1):
        concept_dir = round_dir / concept_name
        concept_dir.mkdir(parents=True)
        sections = [f"# 方向 {index}", ""]
        for section in DIRECTION_REQUIRED_SECTIONS:
            sections.extend([f"## {section}", "", f"{section}的路线 {index}", ""])
        (concept_dir / "concept.md").write_text("\n".join(sections), encoding="utf-8")
    (round_dir / "comparison.html").write_text(
        """<!doctype html><html><head>
<link rel="stylesheet" href="comparison.css"></head>
<body data-preview-mode="direction-comparison">
<article data-concept-card="concept_01"></article>
<article data-concept-card="concept_02"></article>
<article data-concept-card="concept_03"></article>
</body></html>""",
        encoding="utf-8",
    )
    (round_dir / "comparison.css").write_text("body { display: grid; }", encoding="utf-8")


def write_selected_round(root: Path) -> None:
    concept_dir = root / ROUND_002 / "selected_concept"
    concept_dir.mkdir(parents=True)
    sections = ["# 选定高保真方案", ""]
    for section in REQUIRED_SECTIONS:
        sections.extend([f"## {section}", "", f"{section}的完整内容", ""])
    (concept_dir / "concept.md").write_text("\n".join(sections), encoding="utf-8")
    (concept_dir / "preview.html").write_text(
        """<!doctype html><html><head>
<link rel="stylesheet" href="preview.css"></head>
<body data-preview-mode="selected-prototype">
<nav data-preview-nav>导航</nav>
<main data-preview-page="home">首页</main>
<section data-preview-page="key-feature">关键功能页</section>
</body></html>""",
        encoding="utf-8",
    )
    (concept_dir / "preview.css").write_text("body { color: #111; }", encoding="utf-8")


class TwoStageDesignExplorationTests(unittest.TestCase):
    def test_comparison_round_uses_three_docs_and_one_shared_preview(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_two_stage_") as directory:
            root = Path(directory)
            write_comparison_round(root)
            self.assertEqual(
                [],
                validate_preview_round(
                    root,
                    ROUND_001,
                    preview_mode=DESIGN_PREVIEW_MODE_COMPARISON,
                ),
            )

    def test_selection_starts_one_selected_prototype_round(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_two_stage_") as directory:
            root = Path(directory)
            write_comparison_round(root)
            started = begin_exploration(
                base_state(),
                exploration_decision(),
                active_proposal="memory/proposals/product_proposal_v001.md",
                round_reference=ROUND_001,
            )
            waiting = finalize_preview_round(started, root)
            decision = classify_feedback(
                "我选择方案 B。",
                ROUND_001,
                preview_mode=DESIGN_PREVIEW_MODE_COMPARISON,
            )
            selected = apply_feedback_decision(
                waiting,
                decision,
                feedback_record="memory/decisions/design-feedback-001.md",
                selection_record="memory/decisions/design-selection-001.md",
                next_round_reference=ROUND_002,
            )
            self.assertEqual("DESIGN_EXPLORATION", selected["status"])
            self.assertEqual(DESIGN_PREVIEW_MODE_SELECTED, selected["design_preview_mode"])
            self.assertEqual(ROUND_002, selected["active_design_preview_round"])
            self.assertEqual(
                [f"{ROUND_001}/concept_02"],
                selected["selected_design_concept"]["concept_refs"],
            )

    def test_selected_prototype_requires_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_two_stage_") as directory:
            root = Path(directory)
            write_comparison_round(root)
            write_selected_round(root)
            started = begin_exploration(
                base_state(),
                exploration_decision(),
                active_proposal="memory/proposals/product_proposal_v001.md",
                round_reference=ROUND_001,
            )
            waiting = finalize_preview_round(started, root)
            selected = apply_feedback_decision(
                waiting,
                classify_feedback(
                    "我选择方案 B。",
                    ROUND_001,
                    preview_mode=DESIGN_PREVIEW_MODE_COMPARISON,
                ),
                feedback_record="memory/decisions/design-feedback-001.md",
                selection_record="memory/decisions/design-selection-001.md",
                next_round_reference=ROUND_002,
            )
            generating = begin_exploration(
                selected,
                exploration_decision(),
                active_proposal="memory/proposals/product_proposal_v001.md",
                round_reference=ROUND_002,
                preview_mode=DESIGN_PREVIEW_MODE_SELECTED,
            )
            prototype_waiting = finalize_preview_round(generating, root)
            self.assertEqual(
                "waiting_selected_prototype_confirmation",
                prototype_waiting["design_review_status"],
            )
            confirmed = apply_feedback_decision(
                prototype_waiting,
                classify_feedback(
                    "确认这个设计。",
                    ROUND_002,
                    preview_mode=DESIGN_PREVIEW_MODE_SELECTED,
                ),
                feedback_record="memory/decisions/design-feedback-002.md",
            )
            integrated = integrate_feedback_into_proposal(
                confirmed,
                new_proposal_reference="memory/proposals/product_proposal_v002.md",
            )
            self.assertEqual("WAITING_FOR_PRODUCT_REVIEW", integrated["status"])

    def test_selected_prototype_cannot_integrate_before_confirmation(self) -> None:
        state = base_state()
        state.update(
            {
                "status": "PLANNING_REVISION",
                "design_preview_mode": DESIGN_PREVIEW_MODE_SELECTED,
                "design_review_status": "direction_selected",
                "design_feedback_status": "direction_selected",
                "selected_design_concept": {
                    "mode": "single",
                    "concept_refs": [f"{ROUND_001}/concept_02"],
                },
                "design_selection_record": "memory/decisions/design-selection-001.md",
                "exploration_feedback_record": "memory/decisions/design-feedback-001.md",
            }
        )
        with self.assertRaises(ProjectStateError):
            integrate_feedback_into_proposal(
                state,
                new_proposal_reference="memory/proposals/product_proposal_v002.md",
            )


if __name__ == "__main__":
    unittest.main()
