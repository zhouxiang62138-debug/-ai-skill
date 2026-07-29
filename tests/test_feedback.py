"""F5 用户反馈识别、状态迁移和方案版本测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from feedback import (  # noqa: E402
    apply_feedback_decision,
    classify_feedback,
    integrate_feedback_into_proposal,
)
from project_state import ProjectStateError  # noqa: E402
from project_state import parse_project_yaml  # noqa: E402


ROUND_001 = "artifacts/design_previews/round_001"


def waiting_state() -> dict:
    return {
        "schema_version": 4,
        "project_id": "test_feedback",
        "status": "WAITING_FOR_DESIGN_REVIEW",
        "current_iteration": 0,
        "next_role": "planner",
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "requirements_version": 1,
        "active_requirements": "memory/requirements/requirements_v001.yaml",
        "proposal_status": "draft",
        "user_approval_status": "not_requested",
        "proposal_version": 1,
        "active_proposal": "memory/proposals/product_proposal_v001.md",
        "approved_proposal": None,
        "product_spec_status": "not_started",
        "plan_status": "not_started",
        "active_plan": None,
        "plan_approval_status": "not_requested",
        "design_exploration_required": True,
        "exploration_trigger_reasons": ["visual_preferences_undecided"],
        "design_review_status": "waiting_user_selection",
        "design_preview_round": 1,
        "active_design_preview_round": ROUND_001,
        "exploration_generation_attempt": 1,
        "design_feedback_status": "waiting_user_feedback",
        "design_feedback_round": 0,
        "selected_design_concept": None,
        "design_selection_record": None,
        "exploration_feedback_record": None,
    }


class FeedbackClassificationTests(unittest.TestCase):
    def test_direct_single_selection(self) -> None:
        decision = classify_feedback("我选择方案 B。", ROUND_001)
        self.assertEqual("single", decision.action)
        self.assertEqual((f"{ROUND_001}/concept_02",), decision.concept_refs)

    def test_modification_on_one_direction(self) -> None:
        decision = classify_feedback(
            "我喜欢 B，但不要深色模式，减少动画。", ROUND_001
        )
        self.assertEqual("modify", decision.action)
        self.assertFalse(decision.requires_new_preview)

    def test_modification_can_request_new_preview(self) -> None:
        decision = classify_feedback(
            "修改方案 B 的首页，调整后再看预览。", ROUND_001
        )
        self.assertEqual("modify", decision.action)
        self.assertTrue(decision.requires_new_preview)

    def test_multi_direction_blend(self) -> None:
        decision = classify_feedback(
            "使用 A 的首页、B 的统计页和 C 的配色。", ROUND_001
        )
        self.assertEqual("blend", decision.action)
        self.assertEqual(3, len(decision.concept_refs))

    def test_all_rejected(self) -> None:
        decision = classify_feedback("三个都不喜欢，重新给我三个更专业的。", ROUND_001)
        self.assertEqual("reject_all", decision.action)

    def test_continue_discussion(self) -> None:
        decision = classify_feedback("我还不确定，哪个成本最低？", ROUND_001)
        self.assertEqual("discuss", decision.action)

    def test_restore_previous_direction(self) -> None:
        previous = "artifacts/design_previews/round_001/concept_03"
        decision = classify_feedback(f"恢复之前的方案 {previous}", "artifacts/design_previews/round_002")
        self.assertEqual("restore", decision.action)
        self.assertEqual((previous,), decision.concept_refs)

    def test_vague_positive_feedback_is_ambiguous(self) -> None:
        decision = classify_feedback("看起来不错。", ROUND_001)
        self.assertEqual("ambiguous", decision.action)

    def test_preference_is_not_forced_into_selection(self) -> None:
        decision = classify_feedback("我比较喜欢方案 A。", ROUND_001)
        self.assertEqual("ambiguous", decision.action)

    def test_comparing_two_directions_remains_discussion(self) -> None:
        decision = classify_feedback("方案 A 和方案 B 有什么区别？", ROUND_001)
        self.assertEqual("discuss", decision.action)

    def test_excluding_a_and_selecting_b_is_not_conflicting(self) -> None:
        decision = classify_feedback("不要方案 A，我选择方案 B。", ROUND_001)
        self.assertEqual("single", decision.action)
        self.assertEqual((f"{ROUND_001}/concept_02",), decision.concept_refs)

    def test_negative_selection_does_not_become_positive_selection(self) -> None:
        decision = classify_feedback("不选择方案 A，选择方案 B。", ROUND_001)
        self.assertEqual("single", decision.action)
        self.assertEqual((f"{ROUND_001}/concept_02",), decision.concept_refs)

    def test_selecting_and_excluding_same_direction_is_conflicting(self) -> None:
        decision = classify_feedback("选择方案 A，但不要方案 A。", ROUND_001)
        self.assertEqual("conflicting", decision.action)


class FeedbackTransitionTests(unittest.TestCase):
    def apply(
        self,
        text: str,
        *,
        selection: bool = False,
        next_round: str | None = None,
    ) -> dict:
        decision = classify_feedback(text, ROUND_001)
        return apply_feedback_decision(
            waiting_state(),
            decision,
            feedback_record="memory/decisions/design-feedback-001.md",
            selection_record=(
                "memory/decisions/design-selection-001.md" if selection else None
            ),
            next_round_reference=next_round,
        )

    def test_single_selection_enters_planning_revision(self) -> None:
        state = self.apply("我选择方案 B。", selection=True)
        self.assertEqual("PLANNING_REVISION", state["status"])
        self.assertEqual("single", state["selected_design_concept"]["mode"])
        self.assertIsNone(state["approved_proposal"])
        self.assertIsNone(state["active_plan"])

    def test_blend_preserves_all_sources(self) -> None:
        state = self.apply(
            "使用 A 的首页、B 的统计页和 C 的配色。", selection=True
        )
        self.assertEqual("blend", state["selected_design_concept"]["mode"])
        self.assertEqual(3, len(state["selected_design_concept"]["concept_refs"]))

    def test_ambiguous_feedback_continues_waiting(self) -> None:
        state = self.apply("看起来不错。")
        self.assertEqual("WAITING_FOR_DESIGN_REVIEW", state["status"])
        self.assertEqual("ambiguous", state["design_feedback_status"])
        self.assertIsNone(state["design_selection_record"])

    def test_reject_all_starts_next_round_without_overwrite(self) -> None:
        state = self.apply(
            "三个都不喜欢，重新给我三个更专业的。",
            next_round="artifacts/design_previews/round_002",
        )
        self.assertEqual("DESIGN_EXPLORATION", state["status"])
        self.assertEqual(2, state["design_preview_round"])
        self.assertEqual(0, state["exploration_generation_attempt"])
        self.assertEqual("all_rejected", state["design_feedback_status"])

    def test_modified_preview_starts_next_round(self) -> None:
        state = self.apply(
            "修改方案 B 的首页，调整后再看预览。",
            next_round="artifacts/design_previews/round_002",
        )
        self.assertEqual("DESIGN_EXPLORATION", state["status"])
        self.assertEqual("modification_requested", state["design_feedback_status"])

    def test_next_round_must_increment_exactly_once(self) -> None:
        with self.assertRaises(ProjectStateError):
            self.apply(
                "三个都不喜欢，重新给我三个更专业的。",
                next_round="artifacts/design_previews/round_003",
            )

    def test_selection_requires_append_only_record(self) -> None:
        with self.assertRaises(ProjectStateError):
            self.apply("我选择方案 B。")

    def test_restore_can_reference_older_round(self) -> None:
        current = waiting_state()
        current["active_design_preview_round"] = "artifacts/design_previews/round_002"
        current["design_preview_round"] = 2
        text = (
            "恢复之前的方案 "
            "artifacts/design_previews/round_001/concept_03"
        )
        decision = classify_feedback(text, current["active_design_preview_round"])
        state = apply_feedback_decision(
            current,
            decision,
            feedback_record="memory/decisions/design-feedback-002.md",
            selection_record="memory/decisions/design-selection-002.md",
        )
        self.assertEqual("restored", state["selected_design_concept"]["mode"])

    def test_integrated_proposal_uses_new_complete_version(self) -> None:
        selected = self.apply("我选择方案 B。", selection=True)
        integrated = integrate_feedback_into_proposal(
            selected,
            new_proposal_reference="memory/proposals/product_proposal_v002.md",
        )
        self.assertEqual("WAITING_FOR_PRODUCT_REVIEW", integrated["status"])
        self.assertEqual(2, integrated["proposal_version"])
        self.assertEqual(
            "integrated_into_proposal", integrated["design_feedback_status"]
        )
        self.assertIsNone(integrated["approved_proposal"])
        self.assertIsNone(integrated["active_plan"])

    def test_proposal_version_cannot_skip_or_overwrite(self) -> None:
        selected = self.apply("我选择方案 B。", selection=True)
        with self.assertRaises(ProjectStateError):
            integrate_feedback_into_proposal(
                selected,
                new_proposal_reference="memory/proposals/product_proposal_v003.md",
            )
        with self.assertRaises(ProjectStateError):
            integrate_feedback_into_proposal(
                selected,
                new_proposal_reference="memory/proposals/product_proposal_v001.md",
            )


class FeedbackConfigurationTests(unittest.TestCase):
    def test_workflow_contains_all_feedback_routes(self) -> None:
        workflow = parse_project_yaml(
            (REPO_ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
        )
        routes = workflow["failure_routing"]
        for route in (
            "design_direction_selected",
            "design_modification_selected",
            "design_blend_selected",
            "design_previous_direction_restored",
            "all_design_directions_rejected",
            "design_feedback_discussion",
            "design_feedback_ambiguous",
            "design_feedback_conflicting",
        ):
            self.assertIn(route, routes)

    def test_feedback_and_selection_templates_are_distinct(self) -> None:
        feedback_template = (
            REPO_ROOT / "templates" / "design_feedback.md"
        ).read_text(encoding="utf-8")
        selection_template = (
            REPO_ROOT / "templates" / "design_selection.md"
        ).read_text(encoding="utf-8")
        self.assertIn("用户原始反馈", feedback_template)
        self.assertIn("冲突与待澄清项", feedback_template)
        self.assertIn("本记录不构成产品方案批准", selection_template)


if __name__ == "__main__":
    unittest.main()
