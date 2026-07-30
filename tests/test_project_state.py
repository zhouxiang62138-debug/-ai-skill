"""F3 项目状态数据层测试。"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from project_state import (  # noqa: E402
    ProjectStateError,
    assess_v3_migration,
    load_project_state,
    parse_project_yaml,
    serialize_project_state,
    v3_compatibility_view,
    validate_project_state,
    write_project_state_atomic,
)


def make_v4_state() -> dict:
    return {
        "schema_version": 4,
        "project_id": "test_state",
        "project_name": "状态测试",
        "project_type": "web_app",
        "status": "INTAKE",
        "current_iteration": 0,
        "next_role": None,
        "active_module": "first_ask_intake",
        "requirements_status": "draft",
        "proposal_status": "not_started",
        "user_approval_status": "not_requested",
        "product_spec_status": "not_started",
        "plan_status": "not_started",
        "plan_approval_status": "not_requested",
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
    }


class ProjectStateParserTests(unittest.TestCase):
    def test_v6_template_can_be_loaded_and_validated(self) -> None:
        state = load_project_state(REPO_ROOT / "templates" / "project.yaml")
        self.assertEqual(6, state["schema_version"])
        self.assertEqual([], validate_project_state(state))

    def test_nested_data_round_trip(self) -> None:
        state = make_v4_state()
        state["selected_design_concept"] = {
            "mode": "blend",
            "concept_refs": [
                "artifacts/design_previews/round_001/concept_01",
                "artifacts/design_previews/round_001/concept_03",
            ],
            "integration_notes": "首页与图表融合",
        }
        encoded = serialize_project_state(state)
        self.assertEqual(state, parse_project_yaml(encoded))

    def test_duplicate_key_is_rejected(self) -> None:
        with self.assertRaises(ProjectStateError):
            parse_project_yaml("schema_version: 4\nschema_version: 3\n")

    def test_yaml_anchor_is_rejected(self) -> None:
        with self.assertRaises(ProjectStateError):
            parse_project_yaml("schema_version: &version 4\n")


class ProjectStateValidationTests(unittest.TestCase):
    def test_unknown_schema_is_rejected(self) -> None:
        state = make_v4_state()
        state["schema_version"] = 99
        self.assertTrue(validate_project_state(state))

    def test_waiting_plan_review_requires_complete_product_source(self) -> None:
        state = make_v4_state()
        state.update(
            {
                "status": "WAITING_FOR_PLAN_REVIEW",
                "active_module": None,
                "next_role": "planner",
                "requirements_status": "sufficient_for_planning",
                "proposal_status": "approved",
                "product_spec_status": "finalized",
                "plan_status": "waiting_user_review",
                "plan_approval_status": "waiting_explicit_confirmation",
            }
        )
        errors = validate_project_state(state)
        self.assertTrue(any("approved_proposal" in error for error in errors))
        self.assertTrue(any("active_plan" in error for error in errors))

    def test_design_exploration_requires_trigger_source(self) -> None:
        state = make_v4_state()
        state.update(
            {
                "status": "DESIGN_EXPLORATION",
                "active_module": None,
                "next_role": "planner",
                "requirements_status": "sufficient_for_planning",
                "active_requirements": "memory/requirements/requirements_v001.yaml",
                "active_proposal": "memory/proposals/product_proposal_v001.md",
                "design_exploration_required": True,
                "design_review_status": "generating",
                "exploration_generation_attempt": 1,
            }
        )
        errors = validate_project_state(state)
        self.assertTrue(any("exploration_trigger_reasons" in error for error in errors))

    def test_complete_waiting_design_state_is_valid(self) -> None:
        state = make_v4_state()
        state.update(
            {
                "status": "WAITING_FOR_DESIGN_REVIEW",
                "active_module": None,
                "next_role": "planner",
                "requirements_status": "sufficient_for_planning",
                "active_requirements": "memory/requirements/requirements_v001.yaml",
                "active_proposal": "memory/proposals/product_proposal_v001.md",
                "design_exploration_required": True,
                "exploration_trigger_reasons": ["visual_preferences_undecided"],
                "design_review_status": "waiting_user_selection",
                "design_feedback_status": "waiting_user_feedback",
                "active_design_preview_round": "artifacts/design_previews/round_001",
                "exploration_generation_attempt": 1,
            }
        )
        self.assertEqual([], validate_project_state(state))

    def test_approved_for_implementation_requires_plan_approval(self) -> None:
        state = make_v4_state()
        state.update(
            {
                "status": "APPROVED_FOR_IMPLEMENTATION",
                "active_module": None,
                "next_role": "generator",
                "requirements_status": "sufficient_for_planning",
                "proposal_status": "approved",
                "product_spec_status": "finalized",
                "plan_status": "waiting_user_review",
                "plan_approval_status": "waiting_explicit_confirmation",
                "active_requirements": "memory/requirements/requirements_v001.yaml",
                "active_proposal": "memory/proposals/product_proposal_v002.md",
                "approved_proposal": "memory/proposals/product_proposal_v002.md",
                "product_approval_record": "memory/decisions/product-approval-001.md",
                "active_product_spec": "memory/specifications/product_spec_v001.md",
                "active_plan": "memory/plans/plan-001.md",
            }
        )
        errors = validate_project_state(state)
        self.assertTrue(any("approved_plan" in error for error in errors))
        self.assertTrue(any("plan_approval_record" in error for error in errors))

    def test_complete_v4_approval_chain_is_valid(self) -> None:
        state = make_v4_state()
        state.update(
            {
                "status": "APPROVED_FOR_IMPLEMENTATION",
                "active_module": None,
                "next_role": "generator",
                "requirements_status": "sufficient_for_planning",
                "proposal_status": "approved",
                "product_spec_status": "finalized",
                "plan_status": "approved",
                "plan_approval_status": "approved",
                "active_requirements": "memory/requirements/requirements_v001.yaml",
                "active_proposal": "memory/proposals/product_proposal_v002.md",
                "approved_proposal": "memory/proposals/product_proposal_v002.md",
                "product_approval_record": "memory/decisions/product-approval-001.md",
                "active_product_spec": "memory/specifications/product_spec_v001.md",
                "active_plan": "memory/plans/plan-001.md",
                "approved_plan": "memory/plans/plan-001.md",
                "plan_approval_record": "memory/decisions/plan-approval-001.md",
            }
        )
        self.assertEqual([], validate_project_state(state))

    def test_path_escape_is_rejected(self) -> None:
        state = make_v4_state()
        state["active_requirements"] = "../other-project/requirements.yaml"
        with tempfile.TemporaryDirectory(prefix="test_state_") as directory:
            errors = validate_project_state(state, directory)
        self.assertTrue(any("项目目录之外" in error for error in errors))

    def test_selected_concept_source_must_exist_when_checking_paths(self) -> None:
        state = make_v4_state()
        state["selected_design_concept"] = {
            "mode": "single",
            "concept_refs": [
                "artifacts/design_previews/round_001/concept_01"
            ],
            "integration_notes": "选择方案一",
        }
        with tempfile.TemporaryDirectory(prefix="test_state_") as directory:
            errors = validate_project_state(state, directory)
        self.assertTrue(any("不存在的概念目录" in error for error in errors))


class V3CompatibilityTests(unittest.TestCase):
    def make_v3_state(self, status: str = "PLANNING") -> dict:
        return {
            "schema_version": 3,
            "project_id": "test_legacy",
            "status": status,
            "current_iteration": 0,
            "next_role": "planner",
            "active_module": None,
        }

    def test_v3_is_readable_without_mutation(self) -> None:
        state = self.make_v3_state()
        original = copy.deepcopy(state)
        view = v3_compatibility_view(state)
        self.assertEqual(original, state)
        self.assertEqual(3, view["schema_version"])
        self.assertEqual("not_started", view["product_spec_status"])

    def test_v3_planning_project_is_eligible_for_on_demand_migration(self) -> None:
        self.assertEqual(
            "eligible_for_on_demand_migration",
            assess_v3_migration(self.make_v3_state()),
        )

    def test_v3_implementing_project_requires_manual_review(self) -> None:
        state = self.make_v3_state("IMPLEMENTING")
        state["next_role"] = "generator"
        self.assertEqual("manual_review_required", assess_v3_migration(state))

    def test_archived_v3_project_is_not_migrated(self) -> None:
        state = self.make_v3_state("ARCHIVED")
        state["next_role"] = None
        self.assertEqual("archived_do_not_migrate", assess_v3_migration(state))


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_and_reload(self) -> None:
        state = make_v4_state()
        with tempfile.TemporaryDirectory(prefix="test_state_") as directory:
            target = Path(directory) / "project.yaml"
            write_project_state_atomic(target, state)
            self.assertEqual(state, load_project_state(target))

    def test_invalid_state_does_not_overwrite_existing_file(self) -> None:
        state = make_v4_state()
        with tempfile.TemporaryDirectory(prefix="test_state_") as directory:
            target = Path(directory) / "project.yaml"
            target.write_text("sentinel: true\n", encoding="utf-8")
            invalid = copy.deepcopy(state)
            invalid["schema_version"] = 99
            with self.assertRaises(ProjectStateError):
                write_project_state_atomic(target, invalid)
            self.assertEqual("sentinel: true\n", target.read_text(encoding="utf-8"))


class SchemaFileTests(unittest.TestCase):
    def test_schema_files_are_valid_json(self) -> None:
        for version in (3, 4, 5, 6):
            path = REPO_ROOT / "config" / "schemas" / f"project_v{version}.schema.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(version, data["properties"]["schema_version"]["const"])


if __name__ == "__main__":
    unittest.main()
