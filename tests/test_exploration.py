"""F4 产品探索触发、工件校验和恢复测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from exploration import (  # noqa: E402
    REQUIRED_CONCEPTS,
    REQUIRED_SECTIONS,
    apply_explicit_skip,
    assess_exploration_recovery,
    begin_exploration,
    decide_design_exploration,
    finalize_preview_round,
    validate_preview_round,
)
from project_state import ProjectStateError  # noqa: E402
from project_state import parse_project_yaml  # noqa: E402


def requirements_with_design(status: str, completeness: str, routing=None) -> dict:
    return {
        "design_preferences": {
            "status": status,
            "routing": routing,
            "specification_completeness": completeness,
        }
    }


def base_v4_state() -> dict:
    return {
        "schema_version": 4,
        "project_id": "test_exploration",
        "status": "PLANNING",
        "current_iteration": 0,
        "next_role": "planner",
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "active_requirements": "memory/requirements/requirements_v001.yaml",
        "proposal_status": "draft",
        "user_approval_status": "not_requested",
        "product_spec_status": "not_started",
        "plan_status": "not_started",
        "plan_approval_status": "not_requested",
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
        "active_plan": None,
    }


def create_valid_round(root: Path, *, same_routes: bool = False) -> str:
    round_reference = "artifacts/design_previews/round_001"
    round_dir = root / round_reference
    for index, concept in enumerate(REQUIRED_CONCEPTS, 1):
        concept_dir = round_dir / concept
        concept_dir.mkdir(parents=True)
        content: list[str] = [f"# 产品与设计探索方案 {index}", ""]
        for section in REQUIRED_SECTIONS:
            content.append(f"## {section}")
            if same_routes and section in {
                "产品定位",
                "核心优势",
                "方案特色功能",
                "主要用户路径",
            }:
                value = "相同路线"
            else:
                value = f"{section}的方案 {index} 内容"
            content.extend(["", value, ""])
        (concept_dir / "concept.md").write_text(
            "\n".join(content), encoding="utf-8"
        )
        (concept_dir / "preview.html").write_text(
            """<!doctype html>
<html><head><link rel="stylesheet" href="preview.css"></head>
<body>
<nav data-preview-nav>导航</nav>
<main data-preview-page="home">首页</main>
<section data-preview-page="key-feature">关键功能页</section>
</body></html>
""",
            encoding="utf-8",
        )
        (concept_dir / "preview.css").write_text(
            "body { font-family: sans-serif; }\n", encoding="utf-8"
        )
    return round_reference


class ExplorationTriggerTests(unittest.TestCase):
    def test_undecided_visual_direction_requires_exploration(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("undecided", "none", "design_exploration")
        )
        self.assertTrue(decision.required)
        self.assertEqual("exploration_required", decision.action)
        self.assertIn("visual_preferences_undecided", decision.reasons)

    def test_user_request_always_requires_exploration(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("answered", "complete"), user_requested=True
        )
        self.assertTrue(decision.required)
        self.assertIn("user_requested_previews", decision.reasons)

    def test_complete_spec_still_waits_for_skip_confirmation(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("answered", "complete")
        )
        self.assertIsNone(decision.required)
        self.assertEqual("await_skip_confirmation", decision.action)

    def test_complete_spec_and_explicit_approval_can_skip(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("answered", "complete"),
            user_explicitly_approved_skip=True,
        )
        self.assertFalse(decision.required)
        self.assertEqual("skip_allowed", decision.action)

    def test_partial_spec_cannot_skip(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("answered", "partial"),
            user_explicitly_approved_skip=True,
        )
        self.assertTrue(decision.required)
        self.assertIn("skip_rejected_incomplete_specification", decision.reasons)

    def test_conflicting_user_commands_are_rejected(self) -> None:
        with self.assertRaises(ProjectStateError):
            decide_design_exploration(
                requirements_with_design("answered", "complete"),
                user_requested=True,
                user_explicitly_approved_skip=True,
            )


class ExplorationTransitionTests(unittest.TestCase):
    def test_begin_and_finalize_exploration(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("undecided", "none", "design_exploration")
        )
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            started = begin_exploration(
                base_v4_state(),
                decision,
                active_proposal="memory/proposals/product_proposal_v001.md",
                round_reference=reference,
            )
            self.assertEqual("DESIGN_EXPLORATION", started["status"])
            self.assertEqual(1, started["exploration_generation_attempt"])
            waiting = finalize_preview_round(started, root)
            self.assertEqual("WAITING_FOR_DESIGN_REVIEW", waiting["status"])
            self.assertEqual("waiting_user_selection", waiting["design_review_status"])

    def test_invalid_preview_cannot_enter_waiting_state(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("undecided", "none", "design_exploration")
        )
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            started = begin_exploration(
                base_v4_state(),
                decision,
                active_proposal="memory/proposals/product_proposal_v001.md",
                round_reference="artifacts/design_previews/round_001",
            )
            with self.assertRaises(ProjectStateError):
                finalize_preview_round(started, directory)

    def test_explicit_skip_waits_for_product_review(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("answered", "complete"),
            user_explicitly_approved_skip=True,
        )
        state = base_v4_state()
        state["active_proposal"] = "memory/proposals/product_proposal_v001.md"
        skipped = apply_explicit_skip(
            state,
            decision,
            design_skip_record="memory/decisions/design-skip-001.md",
        )
        self.assertEqual("WAITING_FOR_PRODUCT_REVIEW", skipped["status"])
        self.assertIsNone(skipped.get("approved_proposal"))
        self.assertIsNone(skipped["active_plan"])

    def test_exploration_retry_limit_is_enforced(self) -> None:
        decision = decide_design_exploration(
            requirements_with_design("undecided", "none", "design_exploration")
        )
        state = base_v4_state()
        state["exploration_generation_attempt"] = 2
        with self.assertRaises(ProjectStateError):
            begin_exploration(
                state,
                decision,
                active_proposal="memory/proposals/product_proposal_v001.md",
                round_reference="artifacts/design_previews/round_003",
            )


class PreviewRoundValidationTests(unittest.TestCase):
    def test_design_concept_template_contains_all_required_sections(self) -> None:
        template = (REPO_ROOT / "templates" / "design_concept.md").read_text(
            encoding="utf-8"
        )
        for section in REQUIRED_SECTIONS:
            self.assertIn(f"## {section}", template)

    def test_workflow_declares_f4_retry_and_count_rules(self) -> None:
        workflow = parse_project_yaml(
            (REPO_ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(6, workflow["version"])
        self.assertEqual(3, workflow["design_exploration"]["required_concept_count"])
        self.assertEqual(
            2, workflow["design_exploration"]["maximum_generation_attempts"]
        )
        role_policies = parse_project_yaml(
            (REPO_ROOT / "config" / "role_policies.yaml").read_text(encoding="utf-8")
        )
        self.assertEqual(6, role_policies["version"])

    def test_complete_three_direction_round_is_valid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            self.assertEqual([], validate_preview_round(root, reference))

    def test_missing_preview_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            (root / reference / "concept_02" / "preview.css").unlink()
            errors = validate_preview_round(root, reference)
            self.assertTrue(any("concept_02/preview.css" in error for error in errors))

    def test_fourth_concept_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            (root / reference / "concept_04").mkdir()
            errors = validate_preview_round(root, reference)
            self.assertTrue(any("必须恰好包含" in error for error in errors))

    def test_routes_that_only_change_names_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root, same_routes=True)
            errors = validate_preview_round(root, reference)
            self.assertTrue(any("产品路线差异不足" in error for error in errors))

    def test_preview_path_cannot_escape_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            errors = validate_preview_round(directory, "../other-project/round_001")
            self.assertTrue(any("项目目录之外" in error for error in errors))


class ExplorationRecoveryTests(unittest.TestCase):
    def base_state(self, status: str, reference: str | None) -> dict:
        return {
            "status": status,
            "active_design_preview_round": reference,
            "exploration_generation_attempt": 1,
        }

    def test_complete_interrupted_round_can_finalize_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            action = assess_exploration_recovery(
                self.base_state("DESIGN_EXPLORATION", reference), root
            )
            self.assertEqual("finalize_waiting_review", action)

    def test_waiting_state_is_preserved_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            action = assess_exploration_recovery(
                self.base_state("WAITING_FOR_DESIGN_REVIEW", reference), root
            )
            self.assertEqual("continue_waiting_for_user", action)

    def test_missing_artifact_is_resumed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            (root / reference / "concept_03" / "preview.html").unlink()
            action = assess_exploration_recovery(
                self.base_state("DESIGN_EXPLORATION", reference), root
            )
            self.assertEqual("resume_missing_artifacts", action)

    def test_missing_concept_directory_is_resumed_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            root = Path(directory)
            reference = create_valid_round(root)
            concept = root / reference / "concept_03"
            for item in concept.iterdir():
                item.unlink()
            concept.rmdir()
            action = assess_exploration_recovery(
                self.base_state("DESIGN_EXPLORATION", reference), root
            )
            self.assertEqual("resume_missing_artifacts", action)

    def test_retry_limit_routes_to_user(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_exploration_") as directory:
            state = self.base_state(
                "DESIGN_EXPLORATION", "artifacts/design_previews/round_001"
            )
            state["exploration_generation_attempt"] = 2
            action = assess_exploration_recovery(state, directory)
            self.assertEqual("wait_for_user_after_retry_exhausted", action)


if __name__ == "__main__":
    unittest.main()
