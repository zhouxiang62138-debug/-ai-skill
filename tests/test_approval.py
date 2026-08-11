"""F6 产品/Plan 双重批准、撤销、变更控制与 Generator 门禁测试。"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from approval import (  # noqa: E402
    PLAN_SECTIONS,
    PRODUCT_SPEC_SECTIONS,
    approve_plan,
    classify_approval,
    prepare_product_approval_for_plan_review,
    request_plan_revision,
    request_post_implementation_change,
    return_revised_plan_for_review,
    revoke_approval,
    validate_generator_gate,
)
from project_state import ProjectStateError  # noqa: E402


REQUIREMENTS = "memory/requirements/requirements_v001.yaml"
PROPOSAL = "memory/proposals/product_proposal_v002.md"
FEEDBACK = "memory/decisions/design-feedback-001.md"
SELECTION = "memory/decisions/design-selection-001.md"
PRODUCT_APPROVAL = "memory/decisions/product-approval-001.md"
PRODUCT_SPEC = "memory/specifications/product_spec_v001.md"
PLAN_001 = "memory/plans/plan-001.md"
PLAN_002 = "memory/plans/plan-002.md"
PLAN_APPROVAL = "memory/decisions/plan-approval-001.md"
ROUND = "artifacts/design_previews/round_001"
CONCEPT = f"{ROUND}/concept_01"


def product_review_state() -> dict:
    return {
        "schema_version": 4,
        "project_id": "test_approval",
        "status": "WAITING_FOR_PRODUCT_REVIEW",
        "current_iteration": 0,
        "next_role": "planner",
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "requirements_version": 1,
        "active_requirements": REQUIREMENTS,
        "proposal_status": "waiting_user_review",
        "proposal_version": 2,
        "active_proposal": PROPOSAL,
        "approved_proposal": None,
        "user_approval_status": "waiting_explicit_confirmation",
        "product_approval_record": None,
        "approval_revocation_record": None,
        "change_request_record": None,
        "product_spec_status": "not_started",
        "product_spec_version": 0,
        "active_product_spec": None,
        "plan_status": "not_started",
        "plan_version": 0,
        "active_plan": None,
        "approved_plan": None,
        "plan_approval_status": "not_requested",
        "plan_approval_record": None,
        "design_exploration_required": True,
        "exploration_trigger_reasons": ["visual_preferences_undecided"],
        "design_review_status": "integrated_into_proposal",
        # 该夹具覆盖旧审批快照；旧三套完整预览必须显式标注为兼容模式。
        "design_preview_mode": "legacy_full",
        "design_preview_round": 1,
        "active_design_preview_round": ROUND,
        "exploration_generation_attempt": 1,
        "exploration_error_record": None,
        "design_feedback_status": "integrated_into_proposal",
        "design_feedback_round": 1,
        "selected_design_concept": {
            "mode": "single",
            "concept_refs": [CONCEPT],
            "integration_notes": "采用路线一",
        },
        "design_selection_record": SELECTION,
        "design_skip_record": None,
        "exploration_feedback_record": FEEDBACK,
    }


def markdown_with_sections(sections: tuple[str, ...], references: list[str]) -> str:
    source = "\n".join(f"- `{reference}`" for reference in references)
    body = ["# 测试工件"]
    for section in sections:
        body.extend((f"\n## {section}", f"{section}的已确认内容。\n{source}"))
    return "\n".join(body) + "\n"


def write_text(root: Path, reference: str, content: str = "测试内容\n") -> None:
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def prepare_project_tree(root: Path) -> None:
    for reference in (REQUIREMENTS, PROPOSAL, FEEDBACK, SELECTION):
        write_text(root, reference)
    (root / CONCEPT).mkdir(parents=True, exist_ok=True)
    product_sources = [REQUIREMENTS, PROPOSAL, PRODUCT_APPROVAL, FEEDBACK, SELECTION]
    plan_sources = [REQUIREMENTS, PROPOSAL, PRODUCT_APPROVAL, PRODUCT_SPEC, SELECTION]
    write_text(
        root,
        PRODUCT_SPEC,
        markdown_with_sections(PRODUCT_SPEC_SECTIONS, product_sources),
    )
    write_text(root, PLAN_001, markdown_with_sections(PLAN_SECTIONS, plan_sources))
    write_text(
        root,
        PRODUCT_APPROVAL,
        "\n".join([REQUIREMENTS, PROPOSAL, PRODUCT_SPEC, PLAN_001, SELECTION]),
    )


class ApprovalClassificationTests(unittest.TestCase):
    def test_product_confirmation_is_explicit(self) -> None:
        self.assertEqual(
            "approve", classify_approval("确认当前产品方案", "product").action
        )

    def test_start_development_alone_is_ambiguous(self) -> None:
        self.assertEqual(
            "ambiguous", classify_approval("开始开发", "product").action
        )

    def test_positive_comment_is_not_approval(self) -> None:
        self.assertEqual(
            "ambiguous", classify_approval("看起来不错", "plan").action
        )

    def test_plan_confirmation_is_independent(self) -> None:
        self.assertEqual(
            "approve", classify_approval("确认当前开发 Plan", "plan").action
        )

    def test_short_answer_only_works_after_explicit_question(self) -> None:
        self.assertEqual("ambiguous", classify_approval("确认", "plan").action)
        self.assertEqual(
            "approve",
            classify_approval(
                "确认", "plan", responding_to_explicit_question=True
            ).action,
        )

    def test_revision_and_revocation_are_distinct(self) -> None:
        self.assertEqual(
            "revise", classify_approval("这个 Plan 还要修改", "plan").action
        )
        self.assertEqual(
            "revoke", classify_approval("撤销产品批准", "product").action
        )


class ApprovalTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.product_decision = classify_approval("确认当前产品方案", "product")
        self.plan_decision = classify_approval("确认当前开发 Plan", "plan")

    def waiting_plan_state(self) -> dict:
        return prepare_product_approval_for_plan_review(
            product_review_state(),
            self.product_decision,
            product_approval_record=PRODUCT_APPROVAL,
            product_spec_reference=PRODUCT_SPEC,
            plan_reference=PLAN_001,
        )

    def test_product_approval_does_not_release_generator(self) -> None:
        state = self.waiting_plan_state()
        self.assertEqual("WAITING_FOR_PLAN_REVIEW", state["status"])
        self.assertEqual("planner", state["next_role"])
        self.assertIsNone(state["approved_plan"])
        self.assertIsNone(state["plan_approval_record"])

    def test_plan_approval_releases_generator(self) -> None:
        state = approve_plan(
            self.waiting_plan_state(),
            self.plan_decision,
            plan_approval_record=PLAN_APPROVAL,
        )
        self.assertEqual("APPROVED_FOR_IMPLEMENTATION", state["status"])
        self.assertEqual("generator", state["next_role"])
        self.assertEqual(PLAN_001, state["approved_plan"])

    def test_product_decision_cannot_approve_plan(self) -> None:
        with self.assertRaises(ProjectStateError):
            approve_plan(
                self.waiting_plan_state(),
                self.product_decision,
                plan_approval_record=PLAN_APPROVAL,
            )

    def test_plan_revision_requires_new_version(self) -> None:
        revision = classify_approval("这个 Plan 还要修改", "plan")
        state = request_plan_revision(self.waiting_plan_state(), revision)
        self.assertEqual("PLANNING_REVISION", state["status"])
        with self.assertRaises(ProjectStateError):
            return_revised_plan_for_review(state, new_plan_reference=PLAN_001)
        revised = return_revised_plan_for_review(
            state, new_plan_reference=PLAN_002
        )
        self.assertEqual(2, revised["plan_version"])
        self.assertEqual("WAITING_FOR_PLAN_REVIEW", revised["status"])

    def test_product_revocation_invalidates_downstream_pointers(self) -> None:
        state = revoke_approval(
            self.waiting_plan_state(),
            target="product",
            revocation_record="memory/decisions/approval-revocation-001.md",
        )
        self.assertEqual("PLANNING_REVISION", state["status"])
        self.assertIsNone(state["approved_proposal"])
        self.assertIsNone(state["active_product_spec"])
        self.assertIsNone(state["active_plan"])

    def test_started_implementation_requires_change_control(self) -> None:
        state = approve_plan(
            self.waiting_plan_state(),
            self.plan_decision,
            plan_approval_record=PLAN_APPROVAL,
        )
        state["status"] = "IMPLEMENTING"
        with self.assertRaises(ProjectStateError):
            revoke_approval(
                state,
                target="plan",
                revocation_record="memory/decisions/approval-revocation-001.md",
            )
        changed = request_post_implementation_change(
            state,
            change_request_record="memory/decisions/change-request-001.md",
        )
        self.assertEqual("WAITING_FOR_USER", changed["status"])
        self.assertIsNone(changed["next_role"])


class GeneratorGateTests(unittest.TestCase):
    def build_approved_project(self, root: Path) -> dict:
        prepare_project_tree(root)
        product = prepare_product_approval_for_plan_review(
            product_review_state(),
            classify_approval("确认当前产品方案", "product"),
            product_approval_record=PRODUCT_APPROVAL,
            product_spec_reference=PRODUCT_SPEC,
            plan_reference=PLAN_001,
            project_root=root,
        )
        write_text(
            root,
            PLAN_APPROVAL,
            "\n".join(
                [
                    REQUIREMENTS,
                    PROPOSAL,
                    PRODUCT_APPROVAL,
                    PRODUCT_SPEC,
                    PLAN_001,
                    SELECTION,
                ]
            ),
        )
        return approve_plan(
            product,
            classify_approval("确认当前开发 Plan", "plan"),
            plan_approval_record=PLAN_APPROVAL,
            project_root=root,
        )

    def test_complete_source_chain_passes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_approval_") as directory:
            root = Path(directory)
            state = self.build_approved_project(root)
            self.assertEqual([], validate_generator_gate(state, root))

    def test_waiting_plan_state_cannot_run_generator(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_approval_") as directory:
            root = Path(directory)
            prepare_project_tree(root)
            state = prepare_product_approval_for_plan_review(
                product_review_state(),
                classify_approval("确认当前产品方案", "product"),
                product_approval_record=PRODUCT_APPROVAL,
                product_spec_reference=PRODUCT_SPEC,
                plan_reference=PLAN_001,
                project_root=root,
            )
            errors = validate_generator_gate(state, root)
            self.assertTrue(any("APPROVED_FOR_IMPLEMENTATION" in error for error in errors))

    def test_missing_spec_section_blocks_generator(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_approval_") as directory:
            root = Path(directory)
            state = self.build_approved_project(root)
            content = (root / PRODUCT_SPEC).read_text(encoding="utf-8")
            content = content.replace("## 性能要求", "### 性能要求", 1)
            (root / PRODUCT_SPEC).write_text(content, encoding="utf-8")
            errors = validate_generator_gate(state, root)
            self.assertTrue(any("性能要求" in error for error in errors))

    def test_missing_plan_approval_source_blocks_generator(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_approval_") as directory:
            root = Path(directory)
            prepare_project_tree(root)
            state = prepare_product_approval_for_plan_review(
                product_review_state(),
                classify_approval("确认当前产品方案", "product"),
                product_approval_record=PRODUCT_APPROVAL,
                product_spec_reference=PRODUCT_SPEC,
                plan_reference=PLAN_001,
                project_root=root,
            )
            (root / PLAN_APPROVAL).write_text("缺少来源链\n", encoding="utf-8")
            with self.assertRaisesRegex(ProjectStateError, "Plan 批准记录.*未引用来源"):
                approve_plan(
                    state,
                    classify_approval("确认当前开发 Plan", "plan"),
                    plan_approval_record=PLAN_APPROVAL,
                    project_root=root,
                )

    def test_artifact_path_cannot_escape_project(self) -> None:
        state = product_review_state()
        with self.assertRaises(ProjectStateError):
            prepare_product_approval_for_plan_review(
                state,
                classify_approval("确认当前产品方案", "product"),
                product_approval_record=PRODUCT_APPROVAL,
                product_spec_reference="../product_spec_v001.md",
                plan_reference=PLAN_001,
            )


class ApprovalTemplateTests(unittest.TestCase):
    def test_templates_cover_f6_artifacts(self) -> None:
        names = {
            "product_specification.md",
            "plan.md",
            "product_approval.md",
            "plan_approval.md",
            "approval_revocation.md",
            "change_request.md",
        }
        self.assertTrue(
            names.issubset(
                {path.name for path in (REPO_ROOT / "templates").iterdir()}
            )
        )

    def test_spec_and_plan_templates_have_required_headings(self) -> None:
        spec = (REPO_ROOT / "templates" / "product_specification.md").read_text(
            encoding="utf-8"
        )
        plan = (REPO_ROOT / "templates" / "plan.md").read_text(encoding="utf-8")
        for heading in PRODUCT_SPEC_SECTIONS:
            self.assertIn(f"## {heading}", spec)
        for heading in PLAN_SECTIONS:
            self.assertIn(f"## {heading}", plan)


if __name__ == "__main__":
    unittest.main()
