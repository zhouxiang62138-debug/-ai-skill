import copy
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from project_migration import (  # noqa: E402
    inspect_migration,
    migrate_project_file,
    preview_migration,
    rollback_project_file,
    verify_migration,
)
from project_state import (  # noqa: E402
    ProjectStateError,
    load_project_state,
    serialize_project_state,
    validate_project_state,
)


def v4_state(status="INTAKE"):
    return {
        "schema_version": 4,
        "project_id": "test_migration",
        "project_name": "迁移测试",
        "project_type": "application",
        "status": status,
        "current_iteration": 0,
        "next_role": None,
        "active_module": "first_ask_intake" if status == "INTAKE" else None,
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


def v3_state(status="PLANNING"):
    state = v4_state(status)
    state["schema_version"] = 3
    for field in (
        "product_spec_status",
        "plan_status",
        "plan_approval_status",
        "exploration_trigger_reasons",
        "exploration_generation_attempt",
        "design_feedback_status",
        "design_feedback_round",
    ):
        state.pop(field)
    state["next_role"] = "planner"
    return state


class MigrationAssessmentTests(unittest.TestCase):
    def test_v4_and_v5_are_eligible(self):
        self.assertEqual("ELIGIBLE", inspect_migration(v4_state())["status"])
        state = v4_state()
        state["schema_version"] = 5
        self.assertEqual("ELIGIBLE", inspect_migration(state)["status"])

    def test_current_v6_is_idempotent(self):
        state = preview_migration(v4_state())
        self.assertEqual("CURRENT", inspect_migration(state)["status"])
        self.assertEqual(state, preview_migration(state))

    def test_archived_project_remains_read_only(self):
        state = v4_state("ARCHIVED")
        state["next_role"] = None
        assessment = inspect_migration(state)
        self.assertEqual("READ_ONLY", assessment["status"])
        with self.assertRaises(ProjectStateError):
            preview_migration(state)

    def test_active_v3_requires_manual_review(self):
        state = v3_state("IMPLEMENTING")
        state["next_role"] = "generator"
        assessment = inspect_migration(state)
        self.assertEqual("MANUAL_REVIEW", assessment["status"])


class MigrationPreviewTests(unittest.TestCase):
    def test_preview_does_not_mutate_source(self):
        state = v4_state()
        before = copy.deepcopy(state)
        preview = preview_migration(state)
        self.assertEqual(before, state)
        self.assertEqual(6, preview["schema_version"])
        self.assertEqual(1, preview["iteration_sequence"])
        self.assertEqual([], preview["retry_history"])
        self.assertEqual([], validate_project_state(preview))

    def test_waiting_project_disables_automatic_retry(self):
        state = v4_state("WAITING_FOR_USER")
        preview = preview_migration(state)
        self.assertFalse(preview["automatic_retry_allowed"])

    def test_v3_planning_gains_compatible_defaults(self):
        preview = preview_migration(v3_state())
        self.assertEqual("not_started", preview["product_spec_status"])
        self.assertEqual("not_started", preview["plan_status"])
        self.assertEqual([], validate_project_state(preview))


class MigrationTransactionTests(unittest.TestCase):
    def write_project(self, root, state):
        path = root / "project.yaml"
        path.write_text(serialize_project_state(state), encoding="utf-8")
        return path

    def test_migrate_creates_backup_record_and_valid_v6(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.write_project(root, v4_state())
            backup = root / "backups/project-v4.yaml"
            result = migrate_project_file(project, backup)
            self.assertTrue(result["changed"])
            self.assertTrue(backup.is_file())
            self.assertTrue(Path(result["record_path"]).is_file())
            self.assertTrue(verify_migration(project)["valid"])

    def test_repeat_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.write_project(root, v4_state())
            migrate_project_file(project, root / "backups/project-v4.yaml")
            second = migrate_project_file(project, root / "unused-backup.yaml")
            self.assertFalse(second["changed"])
            self.assertEqual("already_v6", second["reason"])

    def test_existing_backup_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.write_project(root, v4_state())
            backup = root / "backup.yaml"
            backup.write_text("user backup", encoding="utf-8")
            with self.assertRaises(ProjectStateError):
                migrate_project_file(project, backup)
            self.assertEqual("user backup", backup.read_text(encoding="utf-8"))

    def test_rollback_restores_old_schema_and_keeps_both_backups(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.write_project(root, v4_state())
            migration_backup = root / "backups/project-v4.yaml"
            migrate_project_file(project, migration_backup)
            pre_rollback = root / "backups/project-v6-before-rollback.yaml"
            result = rollback_project_file(
                project, migration_backup, pre_rollback
            )
            self.assertEqual(4, result["restored_schema_version"])
            self.assertEqual(4, load_project_state(project)["schema_version"])
            self.assertTrue(migration_backup.is_file())
            self.assertTrue(pre_rollback.is_file())


class V6GovernanceStateTests(unittest.TestCase):
    def test_iteration_limit_requires_retry_disabled(self):
        state = preview_migration(v4_state())
        state["current_iteration"] = 5
        state["status"] = "WAITING_FOR_USER"
        state["automatic_retry_allowed"] = True
        self.assertTrue(
            any(
                "automatic_retry_allowed" in item
                for item in validate_project_state(state)
            )
        )

    def test_escalation_cannot_leave_retry_enabled(self):
        state = preview_migration(v4_state())
        state["status"] = "WAITING_FOR_USER"
        state["escalation_record"] = {"reason": "no_progress"}
        state["automatic_retry_allowed"] = True
        self.assertTrue(
            any("升级记录" in item for item in validate_project_state(state))
        )


if __name__ == "__main__":
    unittest.main()
