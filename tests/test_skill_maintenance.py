"""F8.5 外部 Skill 维护边界测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
import shutil
import hashlib
import json
import os
import subprocess
import inspect
import copy
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from project_state import (  # noqa: E402
    ProjectStateError,
    load_project_state,
    validate_project_state,
    write_project_state_atomic,
)
from skill_maintenance import (  # noqa: E402
    authorize_target_path,
    controlled_sync,
    repository_manifest,
    verify_sync_gate,
    resolve_repository_ref,
    RepositoryRef,
    write_sync_report,
    snapshot_repository,
    restore_snapshot,
    SnapshotError,
    SyncTransactionError,
    RollbackResult,
    InstallationValidation,
    validate_installed_repository,
    validate_handoff_record,
    validate_evidence_record,
    normalize_windows_path,
    validate_target_layout,
    persist_sync_failure_state,
    probe_windows_symlink_capability,
    apply_symlink_environment_block,
    resume_symlink_environment_block,
    persist_symlink_environment_block,
    persist_symlink_environment_resume,
    WINDOWS_SYMLINK_BLOCK_REASON,
    WINDOWS_SYMLINK_RESUME_STATUS,
    WINDOWS_SYMLINK_RESUME_ROLE,
    SYNC_REPORT_REQUIRED_FIELDS,
)


def state(working: Path, installed: Path) -> dict:
    return {
        "schema_version": 5, "project_id": "test_skill", "project_type": "skill_maintenance",
        "status": "ACCEPTED", "current_iteration": 0, "next_role": None, "active_module": None,
        "requirements_status": "sufficient_for_planning", "proposal_status": "approved",
        "user_approval_status": "approved", "product_spec_status": "finalized",
        "plan_status": "approved", "plan_approval_status": "approved",
        "exploration_trigger_reasons": [], "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started", "design_feedback_round": 0,
        "targets": {
            "working_repository": {"path": str(working), "access": "read_write"},
            "installed_repository": {"path": str(installed), "access": "read_only_until_final_sync"},
        },
        "sync": {"exclusions": [".git/**", "project.yaml", "memory/**", "evaluation/**", "logs/**", "archive/**", "**/__pycache__/**"]},
        "final_evaluation_status": "PASS",
        "last_evaluation": "evaluation/reports/evaluation-001.md",
        "required_tests_status": "PASS",
        "sync_preflight_diff": [],
        "open_issues": [],
    }


def prepare_project_root(root: Path, project_state: dict) -> Path:
    project = root / "project"
    report = project / "evaluation" / "reports" / "evaluation-001.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# Evaluation\n\nPASS\n", encoding="utf-8")
    write_project_state_atomic(project / "project.yaml", project_state)
    return project


class MaintenanceTemplateSafetyTests(unittest.TestCase):
    def test_default_template_excludes_git_metadata(self) -> None:
        template = (
            REPO_ROOT / "templates" / "project_skill_maintenance.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("    - .git/**\n", template)

    def test_skill_maintenance_targets_support_project_schema_v6(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working"
            installed = root / "installed"
            working.mkdir()
            installed.mkdir()
            value = state(working, installed)
            value.update(
                schema_version=6,
                iteration_sequence=1,
                automatic_retry_allowed=False,
                iteration_metrics=None,
                retry_history=[],
                routing_disagreements=[],
            )
            baseline = repository_manifest(
                installed, value["sync"]["exclusions"]
            )
            project = prepare_project_root(root, value)
            self.assertEqual([], verify_sync_gate(value, baseline, project))


class SymlinkEnvironmentBlockRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.working = self.root / "working"
        self.installed = self.root / "installed"
        self.project.mkdir()
        self.working.mkdir()
        self.installed.mkdir()
        self.evaluating = state(self.working, self.installed)
        self.evaluating.update(
            status="EVALUATING",
            next_role="evaluator",
            f8_5_status="FAIL",
            final_evaluation_status=None,
            required_tests_status=None,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_environment_block_has_distinct_reason_and_schema_valid_null_role(self) -> None:
        blocked = apply_symlink_environment_block(self.evaluating)
        self.assertEqual("BLOCKED", blocked["status"])
        self.assertIsNone(blocked["next_role"])
        self.assertEqual(WINDOWS_SYMLINK_BLOCK_REASON, blocked["blocked_reason"])
        self.assertNotEqual("sync_rollback_failed", blocked["blocked_reason"])
        self.assertEqual(
            "acceptance_environment", blocked["blocked_context"]["category"]
        )
        self.assertEqual([], validate_project_state(blocked))

    def test_environment_block_can_only_start_from_formal_evaluation(self) -> None:
        for status, role in (
            ("PLANNING", "planner"),
            ("ACCEPTED", None),
            ("BLOCKED", None),
        ):
            with self.subTest(status=status, role=role):
                candidate = dict(self.evaluating)
                candidate.update(status=status, next_role=role)
                if status == "BLOCKED":
                    candidate["blocked_reason"] = "sync_rollback_failed"
                with self.assertRaises(ProjectStateError):
                    apply_symlink_environment_block(candidate)

    def test_resume_rejects_wrong_reason_and_missing_privilege(self) -> None:
        blocked = apply_symlink_environment_block(self.evaluating)
        wrong_reason = dict(blocked)
        wrong_reason["blocked_reason"] = "sync_rollback_failed"
        with self.assertRaises(ProjectStateError):
            resume_symlink_environment_block(
                wrong_reason, {"available": True}
            )
        with self.assertRaises(ProjectStateError):
            resume_symlink_environment_block(
                blocked,
                {
                    "available": False,
                    "directory_symlink": False,
                    "file_symlink": False,
                    "errors": [{"winerror": 1314}],
                },
            )

    def test_resume_returns_exact_evaluating_state_and_never_passes_directly(self) -> None:
        blocked = apply_symlink_environment_block(self.evaluating)
        resumed = resume_symlink_environment_block(
            blocked,
            {
                "available": True,
                "directory_symlink": True,
                "file_symlink": True,
                "errors": [],
            },
        )
        self.assertEqual(WINDOWS_SYMLINK_RESUME_STATUS, resumed["status"])
        self.assertEqual(WINDOWS_SYMLINK_RESUME_ROLE, resumed["next_role"])
        self.assertEqual("FAIL", resumed["f8_5_status"])
        self.assertIsNone(resumed["blocked_reason"])
        self.assertIsNone(resumed["blocked_context"])
        self.assertIsNone(resumed["final_evaluation_status"])
        self.assertIsNone(resumed["required_tests_status"])
        self.assertNotIn(resumed["status"], {"ACCEPTED", "PASS"})

    def test_environment_block_is_persisted_by_deterministic_transition(self) -> None:
        project_yaml = self.project / "project.yaml"
        write_project_state_atomic(project_yaml, self.evaluating)
        persisted = persist_symlink_environment_block(project_yaml)
        loaded = load_project_state(project_yaml)
        self.assertEqual(persisted, loaded)
        self.assertEqual("BLOCKED", loaded["status"])
        self.assertIsNone(loaded["next_role"])
        self.assertEqual(WINDOWS_SYMLINK_BLOCK_REASON, loaded["blocked_reason"])

    def test_persisted_resume_rechecks_capability_before_writing(self) -> None:
        project_yaml = self.project / "project.yaml"
        write_project_state_atomic(
            project_yaml, apply_symlink_environment_block(self.evaluating)
        )
        before = project_yaml.read_text(encoding="utf-8")
        with mock.patch(
            "skill_maintenance.probe_windows_symlink_capability",
            return_value={"available": False, "errors": [{"winerror": 1314}]},
        ):
            with self.assertRaises(ProjectStateError):
                persist_symlink_environment_resume(project_yaml)
        self.assertEqual(before, project_yaml.read_text(encoding="utf-8"))

    def test_real_capability_probe_reports_both_required_link_types(self) -> None:
        capability = probe_windows_symlink_capability()
        self.assertIn("directory_symlink", capability)
        self.assertIn("file_symlink", capability)
        self.assertIn("errors", capability)
        self.assertEqual(
            bool(
                capability["directory_symlink"]
                and capability["file_symlink"]
            ),
            capability["available"],
        )

    def test_workflow_declares_exact_environment_recovery_transition(self) -> None:
        workflow = (REPO_ROOT / "config" / "workflow.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("windows_symlink_privilege_missing:", workflow)
        self.assertIn("category: acceptance_environment", workflow)
        self.assertIn("resume_status: EVALUATING", workflow)
        self.assertIn("resume_next_role: evaluator", workflow)
        self.assertIn(
            "test_directory_symlink_escape_is_rejected", workflow
        )
        self.assertIn("test_file_symlink_escape_is_rejected", workflow)


class SkillMaintenanceTests(unittest.TestCase):
    def test_legacy_project_still_loads(self) -> None:
        self.assertEqual([], validate_project_state({"schema_version": 3, "project_id": "legacy", "status": "PLANNING", "current_iteration": 0, "next_role": "planner", "active_module": None}))

    def test_declared_working_path_is_writable_but_installed_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); working = root / "working"; installed = root / "installed"
            working.mkdir(); installed.mkdir(); value = state(working, installed)
            self.assertEqual([], validate_project_state(value))
            self.assertEqual(working / "scripts/x.py", authorize_target_path(value, "working_repository", working / "scripts/x.py", write=True))
            with self.assertRaises(ProjectStateError):
                authorize_target_path(value, "installed_repository", installed / "SKILL.md", write=True)
            with self.assertRaises(ProjectStateError):
                authorize_target_path(value, "working_repository", root / "other/x.py", write=False)

    def test_non_controlled_service_cannot_authorize_install_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working = root / "working"
            installed = root / "installed"
            working.mkdir()
            installed.mkdir()
            with self.assertRaises(ProjectStateError):
                authorize_target_path(
                    state(working, installed),
                    "installed_repository",
                    installed / "SKILL.md",
                    write=True,
                )

    def test_controlled_sync_has_no_source_or_destination_path_override(self) -> None:
        parameters = inspect.signature(controlled_sync).parameters
        self.assertNotIn("source_repository", parameters)
        self.assertNotIn("destination_repository", parameters)
        self.assertNotIn("working_repository", parameters)
        self.assertNotIn("installed_repository", parameters)

    def test_unknown_install_change_blocks_and_exclusions_do_not_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); working = root / "working"; installed = root / "installed"; backup = root / "backup"
            working.mkdir(); installed.mkdir()
            (working / "SKILL.md").write_text("---\nname: new\n---\n", encoding="utf-8")
            (working / "UPPER_REPORT.md").write_text("report", encoding="utf-8")
            cache = working / "scripts" / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "generated.pyc").write_bytes(b"cache")
            (working / "project.yaml").write_text("state", encoding="utf-8")
            (installed / "SKILL.md").write_text("---\nname: old\n---\n", encoding="utf-8")
            value = state(working, installed); baseline = repository_manifest(installed, value["sync"]["exclusions"])
            self.assertEqual([], verify_sync_gate(value, baseline))
            project = prepare_project_root(root, value)
            result = controlled_sync(
                value, baseline, backup, project_root=project
            )
            self.assertIn("SKILL.md", result["copied"])
            self.assertIn("UPPER_REPORT.md", result["copied"])
            installed_manifest = repository_manifest(
                installed, value["sync"]["exclusions"]
            )
            self.assertIn("UPPER_REPORT.md", installed_manifest)
            self.assertNotIn("scripts/__pycache__/generated.pyc", installed_manifest)
            self.assertFalse((installed / "project.yaml").exists())
            (installed / "local.txt").write_text("user", encoding="utf-8")
            self.assertTrue(verify_sync_gate(value, baseline))

    def test_structured_reference_rejects_escape_and_report_is_project_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); working = root / "working"; installed = root / "installed"
            working.mkdir(); installed.mkdir(); value = state(working, installed)
            self.assertEqual(working / "a.py", resolve_repository_ref(value, RepositoryRef("working_repository", "a.py"), write=True))
            for reference in (RepositoryRef("unknown", "a.py"), RepositoryRef("working_repository", "../a.py"), RepositoryRef("working_repository", "C:/x.py")):
                with self.assertRaises(ProjectStateError): resolve_repository_ref(value, reference)
            json_path, md_path = write_sync_report(root, {"result": "PASS", "copied": ["SKILL.md"], "excluded": ["project.yaml"]})
            self.assertTrue(json_path.is_file() and md_path.is_file())


class SnapshotFailureInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "a.txt").write_text("A", encoding="utf-8")
        (self.source / "b.txt").write_text("B", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_snapshot_directory_creation_failure_is_invalid(self) -> None:
        backup = self.root / "backup"
        original = Path.mkdir
        def fail(path, *args, **kwargs):
            if path == backup: raise OSError("mkdir injected")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "mkdir", fail):
            with self.assertRaises(SnapshotError) as caught:
                snapshot_repository(self.source, backup, [])
        self.assertEqual("create_directory", caught.exception.stage)

    def test_snapshot_copy_midway_failure_marks_invalid(self) -> None:
        backup = self.root / "backup"; original = shutil.copy2; calls = 0
        def fail(source, target, *args, **kwargs):
            nonlocal calls; calls += 1
            if calls == 2: raise OSError("copy injected")
            return original(source, target, *args, **kwargs)
        with mock.patch("skill_maintenance.shutil.copy2", side_effect=fail):
            with self.assertRaises(SnapshotError) as caught:
                snapshot_repository(self.source, backup, [])
        self.assertEqual("copy_files", caught.exception.stage)
        self.assertTrue((backup / ".snapshot-invalid.json").is_file())
        self.assertFalse((backup / "manifest.sha256").exists())

    def test_snapshot_manifest_write_failure_is_invalid(self) -> None:
        backup = self.root / "backup"; original = Path.write_text
        def fail(path, *args, **kwargs):
            if path.name == "manifest.json": raise OSError("manifest injected")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "write_text", fail):
            with self.assertRaises(SnapshotError) as caught:
                snapshot_repository(self.source, backup, [])
        self.assertEqual("write_manifest", caught.exception.stage)

    def test_snapshot_digest_write_failure_is_invalid(self) -> None:
        backup = self.root / "backup"; original = Path.write_text
        def fail(path, *args, **kwargs):
            if path.name == "manifest.sha256": raise OSError("digest injected")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "write_text", fail):
            with self.assertRaises(SnapshotError) as caught:
                snapshot_repository(self.source, backup, [])
        self.assertEqual("write_digest", caught.exception.stage)

    def test_snapshot_hash_calculation_failure_is_invalid(self) -> None:
        backup = self.root / "backup"
        with mock.patch(
            "skill_maintenance.repository_manifest",
            side_effect=OSError("hash injected"),
        ):
            with self.assertRaises(SnapshotError) as caught:
                snapshot_repository(self.source, backup, [])
        self.assertEqual("calculate_hashes", caught.exception.stage)
        self.assertTrue((backup / ".snapshot-invalid.json").is_file())


class RollbackTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.working = self.root / "working"; self.installed = self.root / "installed"
        self.working.mkdir(); self.installed.mkdir()
        (self.working / "a.txt").write_text("new-a", encoding="utf-8")
        (self.working / "b.txt").write_text("new-b", encoding="utf-8")
        (self.installed / "a.txt").write_text("old-a", encoding="utf-8")
        self.value = state(self.working, self.installed)
        self.baseline = repository_manifest(self.installed, self.value["sync"]["exclusions"])
        self.project = prepare_project_root(self.root, self.value)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_sync_failure_after_new_file_rolls_back_with_matching_hashes(self) -> None:
        original = shutil.copy2; calls = 0
        def fail(source, target, *args, **kwargs):
            nonlocal calls; calls += 1
            if calls == 5: raise OSError("commit injected")
            return original(source, target, *args, **kwargs)
        with mock.patch("skill_maintenance.shutil.copy2", side_effect=fail):
            with self.assertRaises(SyncTransactionError) as caught:
                controlled_sync(
                    self.value,
                    self.baseline,
                    self.root / "backup",
                    project_root=self.project,
                )
        error = caught.exception
        self.assertEqual("FAIL", error.result["result"])
        self.assertTrue(error.result["rollback"].success)
        self.assertEqual(self.baseline, repository_manifest(self.installed, self.value["sync"]["exclusions"]))
        self.assertFalse((self.installed / "b.txt").exists())
        self.assertEqual("FAIL", error.project_state["last_sync_status"])

    def test_sync_first_staging_copy_failure_rolls_back_to_fail(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "prepared-backup", [])
        original = shutil.copy2
        calls = 0
        def fail_once(source, target, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("first staging copy injected")
            return original(source, target, *args, **kwargs)
        with mock.patch("skill_maintenance.snapshot_repository", return_value=snapshot), \
             mock.patch("skill_maintenance.shutil.copy2", side_effect=fail_once):
            with self.assertRaises(SyncTransactionError) as caught:
                controlled_sync(
                    self.value,
                    self.baseline,
                    self.root / "unused-backup",
                    project_root=self.project,
                )
        self.assertEqual("FAIL", caught.exception.result["result"])
        self.assertTrue(caught.exception.result["rollback"].success)
        self.assertEqual(self.baseline, repository_manifest(self.installed, self.value["sync"]["exclusions"]))

    def test_restore_copy_failure_is_blocked_result(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "backup", [])
        (self.installed / "a.txt").write_text("changed", encoding="utf-8")
        with mock.patch("skill_maintenance.shutil.copy2", side_effect=OSError("restore injected")):
            result = restore_snapshot(self.installed, snapshot, [])
        self.assertFalse(result.success)
        self.assertIn("restore injected", result.error or "")

    def test_restore_delete_new_file_failure_is_blocked_result(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "backup", [])
        new_file = self.installed / "new.txt"
        new_file.write_text("new", encoding="utf-8")
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path == new_file:
                raise OSError("unlink injected")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "unlink", fail):
            result = restore_snapshot(self.installed, snapshot, [])
        self.assertFalse(result.success)
        self.assertTrue(new_file.exists())

    def test_restore_detects_extra_file_during_verification(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "backup", [])
        original = repository_manifest
        calls = 0
        def manifest(root, exclusions):
            nonlocal calls
            calls += 1
            value = original(root, exclusions)
            if calls == 2:
                value["late-extra.txt"] = "unexpected"
            return value
        with mock.patch("skill_maintenance.repository_manifest", side_effect=manifest):
            result = restore_snapshot(self.installed, snapshot, [])
        self.assertFalse(result.success)
        self.assertEqual(("late-extra.txt",), result.extra)

    def test_rollback_copy_failure_enters_blocked(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "prepared-backup", [])
        failed = RollbackResult(False, error="restore injected")
        with mock.patch("skill_maintenance.snapshot_repository", return_value=snapshot), \
             mock.patch("skill_maintenance.restore_snapshot", return_value=failed), \
             mock.patch("skill_maintenance.shutil.copy2", side_effect=OSError("commit injected")):
            with self.assertRaises(SyncTransactionError) as caught:
                controlled_sync(
                    self.value,
                    self.baseline,
                    self.root / "backup",
                    project_root=self.project,
                )
        self.assertEqual("BLOCKED", caught.exception.result["result"])
        self.assertEqual("BLOCKED", caught.exception.project_state["status"])
        self.assertEqual("sync_rollback_failed", caught.exception.project_state["blocked_reason"])

    def test_rollback_failure_is_atomically_persisted_as_blocked(self) -> None:
        project_yaml = self.project / "project.yaml"
        write_project_state_atomic(project_yaml, self.value)
        persist_sync_failure_state(
            project_yaml,
            self.value,
            RollbackResult(False, error="verification failed"),
        )
        persisted = load_project_state(project_yaml)
        self.assertEqual("BLOCKED", persisted["status"])
        self.assertIsNone(persisted["next_role"])
        self.assertEqual("BLOCKED", persisted["last_sync_status"])
        self.assertEqual(
            "sync_rollback_failed", persisted["blocked_reason"]
        )

    def test_restore_detects_missing_file(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "backup", [])
        (Path(snapshot["files_root"]) / "a.txt").unlink()
        result = restore_snapshot(self.installed, snapshot, [])
        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_restore_detects_hash_mismatch(self) -> None:
        snapshot = snapshot_repository(self.installed, self.root / "backup", [])
        (Path(snapshot["files_root"]) / "a.txt").write_text("corrupt", encoding="utf-8")
        result = restore_snapshot(self.installed, snapshot, [])
        self.assertFalse(result.success)
        self.assertEqual(("a.txt",), result.mismatched)

    def test_restore_preserves_excluded_preexisting_file(self) -> None:
        (self.installed / "logs").mkdir(); (self.installed / "logs" / "keep.txt").write_text("keep", encoding="utf-8")
        snapshot = snapshot_repository(self.installed, self.root / "backup", ["logs/**"])
        (self.installed / "new.txt").write_text("remove", encoding="utf-8")
        result = restore_snapshot(self.installed, snapshot, ["logs/**"])
        self.assertTrue(result.success)
        self.assertEqual("keep", (self.installed / "logs" / "keep.txt").read_text(encoding="utf-8"))
        self.assertFalse((self.installed / "new.txt").exists())


class InstallationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.working = self.root / "working"
        self.installed = self.root / "installed"
        self.working.mkdir()
        self.installed.mkdir()
        self.value = state(self.working, self.installed)
        self.project = prepare_project_root(self.root, self.value)
        skill = "---\nname: test\n---\n"
        (self.working / "SKILL.md").write_text(skill, encoding="utf-8")
        (self.installed / "SKILL.md").write_text(skill, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_installed_validation_passes_for_exact_loadable_copy(self) -> None:
        result = validate_installed_repository(self.working, self.installed, [])
        self.assertTrue(result.success)
        self.assertEqual("PASS", result.load_check)

    def test_installed_validation_detects_missing_mismatch_and_unknown(self) -> None:
        (self.working / "missing.py").write_text("expected", encoding="utf-8")
        (self.working / "SKILL.md").write_text("---\nchanged\n", encoding="utf-8")
        (self.installed / "unknown.py").write_text("unknown", encoding="utf-8")
        result = validate_installed_repository(self.working, self.installed, [])
        self.assertFalse(result.success)
        self.assertEqual(("missing.py",), result.missing)
        self.assertEqual(("SKILL.md",), result.mismatched)
        self.assertEqual(("unknown.py",), result.unexpected)

    def test_installed_validation_rejects_project_artifacts(self) -> None:
        (self.installed / "project.yaml").write_text("forbidden", encoding="utf-8")
        result = validate_installed_repository(
            self.working, self.installed, ["project.yaml"]
        )
        self.assertFalse(result.success)
        self.assertEqual(("project.yaml",), result.forbidden)

    def test_sync_validation_failure_rolls_back_and_cannot_pass(self) -> None:
        (self.working / "SKILL.md").write_text("---\nnew\n", encoding="utf-8")
        value = state(self.working, self.installed)
        baseline = repository_manifest(self.installed, value["sync"]["exclusions"])
        failed = InstallationValidation(False, load_check="FAIL")
        with mock.patch(
            "skill_maintenance.validate_installed_repository", return_value=failed
        ):
            with self.assertRaises(SyncTransactionError) as caught:
                controlled_sync(
                    value,
                    baseline,
                    self.root / "backup",
                    project_root=self.project,
                )
        self.assertEqual("FAIL", caught.exception.result["result"])
        self.assertTrue(caught.exception.result["rollback"].success)
        self.assertEqual(
            baseline,
            repository_manifest(self.installed, value["sync"]["exclusions"]),
        )


class StructuredArtifactProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.working = self.root / "working"
        self.installed = self.root / "installed"
        self.project.mkdir()
        self.working.mkdir()
        self.installed.mkdir()
        self.value = state(self.working, self.installed)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_generator_handoff_accepts_structured_working_reference(self) -> None:
        handoff = {
            "changed_project_artifacts": [
                {"repository": "project", "path": "artifacts/build.json"}
            ],
            "changed_target_files": [
                {"repository": "working_repository", "path": "scripts/example.py"}
            ],
            "verification_artifacts": [
                {"repository": "project", "path": "artifacts/evidence/test.json"}
            ],
        }
        self.assertEqual(
            [],
            validate_handoff_record(
                self.value, self.project, handoff, role="generator"
            ),
        )

    def test_generator_handoff_rejects_installed_write(self) -> None:
        handoff = {
            "changed_project_artifacts": [],
            "changed_target_files": [
                {"repository": "installed_repository", "path": "SKILL.md"}
            ],
            "verification_artifacts": [],
        }
        errors = validate_handoff_record(
            self.value, self.project, handoff, role="generator"
        )
        self.assertTrue(any("working_repository" in error for error in errors))

    def test_handoff_rejects_unknown_absolute_and_parent_paths(self) -> None:
        invalid = (
            {"repository": "unknown", "path": "a.py"},
            {"repository": "working_repository", "path": "C:/outside.py"},
            {"repository": "working_repository", "path": "../outside.py"},
        )
        for reference in invalid:
            with self.subTest(reference=reference):
                handoff = {
                    "changed_project_artifacts": [],
                    "changed_target_files": [reference],
                    "verification_artifacts": [],
                }
                self.assertTrue(
                    validate_handoff_record(
                        self.value, self.project, handoff, role="generator"
                    )
                )

    def test_evidence_separates_project_file_and_target(self) -> None:
        evidence = {
            "evidence_file": {
                "repository": "project",
                "path": "artifacts/evidence/evidence-001.json",
            },
            "target": {
                "repository": "installed_repository",
                "path": "SKILL.md",
            },
            "target_sha256": "a" * 64,
            "command": "python verify.py",
            "result": "PASS",
            "write_intent": False,
        }
        self.assertEqual(
            [],
            validate_evidence_record(
                self.value, self.project, evidence, role="evaluator"
            ),
        )

    def test_generator_evidence_rejects_installed_write_intent(self) -> None:
        evidence = {
            "evidence_file": {
                "repository": "project",
                "path": "artifacts/evidence/evidence-001.json",
            },
            "target": {
                "repository": "installed_repository",
                "path": "SKILL.md",
            },
            "target_sha256": "a" * 64,
            "command": "copy",
            "result": "PASS",
            "write_intent": True,
        }
        errors = validate_evidence_record(
            self.value, self.project, evidence, role="generator"
        )
        self.assertTrue(any("不得" in error for error in errors))

    def test_handoff_and_evidence_schema_files_are_valid_json(self) -> None:
        for name in ("handoff_v1.schema.json", "evidence_v1.schema.json"):
            with self.subTest(name=name):
                schema = json.loads(
                    (
                        REPO_ROOT / "config" / "schemas" / name
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual("object", schema["type"])


class WindowsPathSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.working = self.root / "working"
        self.installed = self.root / "installed"
        self.outside = self.root / "outside"
        for directory in (
            self.project, self.working, self.installed, self.outside
        ):
            directory.mkdir()
        self.value = state(self.working, self.installed)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_separator_case_tail_and_dot_normalize_to_same_path(self) -> None:
        canonical = normalize_windows_path(self.working)
        variants = [
            str(self.working).replace("\\", "/") + "/",
            str(self.working / "."),
            str(self.working).swapcase(),
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertEqual(canonical, normalize_windows_path(variant))

    def test_parent_unc_and_long_path_prefix_are_rejected(self) -> None:
        invalid = [
            str(self.working / ".." / "outside"),
            r"\\server\share\skill",
            r"\\?\C:\skill",
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ProjectStateError):
                    normalize_windows_path(value)

    def test_nested_and_identical_repository_layouts_are_rejected(self) -> None:
        identical = state(self.working, self.working)
        self.assertTrue(validate_target_layout(identical, self.project))
        nested = state(self.working, self.working / "installed")
        (self.working / "installed").mkdir()
        errors = validate_target_layout(nested, self.project)
        self.assertTrue(any("内部" in error for error in errors))

    def test_directory_symlink_escape_is_rejected(self) -> None:
        link = self.working / "linked"
        try:
            os.symlink(self.outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"当前 Windows 权限不允许目录符号链接：{exc}")
        with self.assertRaises(ProjectStateError):
            authorize_target_path(
                self.value, "working_repository", link / "escape.py", write=True
            )

    def test_file_symlink_escape_is_rejected(self) -> None:
        outside_file = self.outside / "secret.txt"
        outside_file.write_text("secret", encoding="utf-8")
        link = self.working / "linked.txt"
        try:
            os.symlink(outside_file, link)
        except OSError as exc:
            self.skipTest(f"当前 Windows 权限不允许文件符号链接：{exc}")
        with self.assertRaises(ProjectStateError):
            authorize_target_path(
                self.value, "working_repository", link, write=True
            )

    def test_junction_escape_is_rejected(self) -> None:
        junction = self.working / "junction"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(self.outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            self.skipTest(f"当前 Windows 环境无法创建 Junction：{result.stderr}")
        with self.assertRaises(ProjectStateError):
            authorize_target_path(
                self.value,
                "working_repository",
                junction / "escape.py",
                write=True,
            )

    def test_repository_root_alias_is_rejected(self) -> None:
        alias = self.root / "working-alias"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(self.working)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            self.skipTest(f"当前 Windows 环境无法创建 Junction：{result.stderr}")
        aliased = state(alias, self.installed)
        self.assertTrue(validate_target_layout(aliased, self.project))

    def test_link_swap_after_validation_is_rejected_on_recheck(self) -> None:
        parent = self.installed / "late"
        parent.mkdir()
        candidate = parent / "file.txt"
        self.assertEqual(
            candidate.resolve(),
            authorize_target_path(
                self.value, "installed_repository", candidate, write=False
            ),
        )
        parent.rmdir()
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(parent), str(self.outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            self.skipTest(f"当前 Windows 环境无法创建 Junction：{result.stderr}")
        with self.assertRaises(ProjectStateError):
            authorize_target_path(
                self.value, "installed_repository", candidate, write=False
            )

    def test_simulated_file_symlink_resolution_escape_is_rejected(self) -> None:
        outside_file = self.outside / "secret.txt"
        with mock.patch(
            "skill_maintenance.normalize_windows_path",
            side_effect=[self.working.resolve(), outside_file.resolve()],
        ):
            with self.assertRaises(ProjectStateError):
                authorize_target_path(
                    self.value,
                    "working_repository",
                    self.working / "linked.txt",
                    write=True,
                )

    def test_unresolvable_real_path_is_rejected_by_default(self) -> None:
        with mock.patch.object(
            Path, "resolve", side_effect=OSError("resolution injected")
        ):
            with self.assertRaises(ProjectStateError):
                normalize_windows_path(self.working)


class FinalSyncGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.working = self.root / "working"
        self.installed = self.root / "installed"
        self.working.mkdir()
        self.installed.mkdir()
        skill = "---\nname: gate-test\n---\n"
        (self.working / "SKILL.md").write_text(skill, encoding="utf-8")
        (self.installed / "SKILL.md").write_text(skill, encoding="utf-8")
        self.value = state(self.working, self.installed)
        self.project = prepare_project_root(self.root, self.value)
        self.baseline = repository_manifest(
            self.installed, self.value["sync"]["exclusions"]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_gate_rejects_missing_or_non_pass_evaluation(self) -> None:
        for field, value in (
            ("final_evaluation_status", None),
            ("final_evaluation_status", "FAIL"),
            ("last_evaluation", None),
        ):
            with self.subTest(field=field, value=value):
                candidate = dict(self.value)
                candidate[field] = value
                self.assertTrue(
                    verify_sync_gate(candidate, self.baseline, self.project)
                )

    def test_gate_rejects_critical_issue_failed_tests_and_missing_diff(self) -> None:
        candidates = []
        failed_tests = dict(self.value)
        failed_tests["required_tests_status"] = "FAIL"
        candidates.append(failed_tests)
        missing_diff = dict(self.value)
        missing_diff["sync_preflight_diff"] = None
        candidates.append(missing_diff)
        critical = dict(self.value)
        critical["open_issues"] = [
            {"severity": "critical", "status": "OPEN"}
        ]
        candidates.append(critical)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assertTrue(
                    verify_sync_gate(candidate, self.baseline, self.project)
                )

    def test_gate_rejects_unknown_install_change(self) -> None:
        (self.installed / "unknown.txt").write_text("user", encoding="utf-8")
        errors = verify_sync_gate(self.value, self.baseline, self.project)
        self.assertTrue(any("未知修改" in error for error in errors))

    def test_gate_rejects_targets_that_differ_from_project_yaml(self) -> None:
        for repository in ("working_repository", "installed_repository"):
            with self.subTest(repository=repository):
                undeclared = self.root / f"undeclared-{repository}"
                undeclared.mkdir()
                candidate = copy.deepcopy(self.value)
                candidate["targets"][repository]["path"] = str(undeclared)
                errors = verify_sync_gate(
                    candidate, self.baseline, self.project
                )
                self.assertTrue(
                    any(
                        f"{repository} 与 project.yaml 声明不一致" in error
                        for error in errors
                    )
                )

    def test_gate_rejects_missing_project_yaml_unique_state_source(self) -> None:
        project = self.root / "project-without-state"
        evaluation = (
            project / "evaluation" / "reports" / "evaluation-001.md"
        )
        evaluation.parent.mkdir(parents=True)
        evaluation.write_text("PASS", encoding="utf-8")
        errors = verify_sync_gate(self.value, self.baseline, project)
        self.assertTrue(
            any("缺少唯一状态源 project.yaml" in error for error in errors)
        )

    def test_controlled_sync_requires_project_root_and_report(self) -> None:
        with self.assertRaises(ProjectStateError):
            controlled_sync(
                self.value, self.baseline, self.root / "backup"
            )

    def test_snapshot_failure_prevents_install_commit(self) -> None:
        (self.working / "SKILL.md").write_text(
            "---\nname: changed\n---\n", encoding="utf-8"
        )
        baseline = repository_manifest(
            self.installed, self.value["sync"]["exclusions"]
        )
        with mock.patch(
            "skill_maintenance.snapshot_repository",
            side_effect=SnapshotError(
                "write_manifest", "backup invalid", self.root / "backup"
            ),
        ):
            with self.assertRaises(SyncTransactionError) as caught:
                controlled_sync(
                    self.value,
                    baseline,
                    self.root / "backup",
                    project_root=self.project,
                )
        self.assertEqual("FAIL", caught.exception.result["result"])
        self.assertFalse(caught.exception.result["rollback_triggered"])
        self.assertEqual(
            baseline,
            repository_manifest(
                self.installed, self.value["sync"]["exclusions"]
            ),
        )

    def test_repeat_sync_is_idempotent_and_reports_no_changes(self) -> None:
        result = controlled_sync(
            self.value,
            self.baseline,
            self.root / "backup",
            project_root=self.project,
        )
        self.assertEqual("PASS", result["result"])
        self.assertTrue(result["no_changes"])
        self.assertIsNone(result["backup"])
        self.assertEqual([], result["copied"])
        self.assertTrue(all(Path(item).is_file() for item in result["reports"]))
        self.assertEqual(
            self.baseline,
            repository_manifest(
                self.installed, self.value["sync"]["exclusions"]
            ),
        )

    def test_json_report_write_failure_does_not_create_success_marker(self) -> None:
        original = Path.write_text
        def fail(path, *args, **kwargs):
            if path.name.endswith(".json.tmp"):
                raise OSError("json report injected")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "write_text", fail):
            with self.assertRaises(OSError):
                write_sync_report(self.project, {"result": "PASS"})
        self.assertEqual([], list((self.project / "reports" / "sync").glob("*.json")))

    def test_markdown_report_write_failure_does_not_create_success_marker(self) -> None:
        original = Path.write_text
        def fail(path, *args, **kwargs):
            if path.name.endswith(".md.tmp"):
                raise OSError("markdown report injected")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "write_text", fail):
            with self.assertRaises(OSError):
                write_sync_report(self.project, {"result": "PASS"})
        self.assertEqual([], list((self.project / "reports" / "sync").glob("*.json")))

    def test_report_json_and_markdown_share_fact_digest(self) -> None:
        report = {
            "result": "FAIL",
            "copied": ["SKILL.md"],
            "excluded": ["project.yaml"],
            "rollback_triggered": True,
        }
        json_path, md_path = write_sync_report(self.root, report)
        canonical = json_path.read_text(encoding="utf-8").rstrip("\n")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertIn(
            f"sync-fact-sha256:{digest}",
            md_path.read_text(encoding="utf-8"),
        )

    def test_sync_report_contains_every_required_field_in_both_formats(self) -> None:
        json_path, md_path = write_sync_report(
            self.root,
            {
                "result": "FAIL",
                "reason": "injected",
                "rollback_triggered": True,
            },
        )
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(set(), set(SYNC_REPORT_REQUIRED_FIELDS) - set(payload))
        markdown = md_path.read_text(encoding="utf-8")
        for field in SYNC_REPORT_REQUIRED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(f'"{field}"', markdown)

    def test_success_report_matches_actual_install_manifest(self) -> None:
        (self.working / "SKILL.md").write_text(
            "---\nname: changed\n---\n", encoding="utf-8"
        )
        baseline = repository_manifest(
            self.installed, self.value["sync"]["exclusions"]
        )
        result = controlled_sync(
            self.value,
            baseline,
            self.root / "backup",
            project_root=self.project,
        )
        report = json.loads(
            Path(result["reports"][0]).read_text(encoding="utf-8")
        )
        actual = repository_manifest(
            self.installed, self.value["sync"]["exclusions"]
        )
        expected = repository_manifest(
            self.working, self.value["sync"]["exclusions"]
        )
        self.assertEqual(expected, actual)
        self.assertEqual(sorted(expected), report["expected_files"])
        self.assertEqual(result["copied"], report["copied"])
        self.assertEqual([], report["post_sync_diff"])
        self.assertTrue(report["installation_validation"]["success"])

    def test_report_write_failure_prevents_sync_success(self) -> None:
        (self.working / "SKILL.md").write_text("---\nnew\n", encoding="utf-8")
        value = state(self.working, self.installed)
        baseline = repository_manifest(self.installed, value["sync"]["exclusions"])
        with mock.patch(
            "skill_maintenance.write_sync_report",
            side_effect=OSError("report injected"),
        ):
            with self.assertRaises(SyncTransactionError) as caught:
                controlled_sync(
                    value,
                    baseline,
                    self.root / "backup",
                    project_root=self.root / "project",
                )
        self.assertEqual("FAIL", caught.exception.result["result"])
        self.assertEqual(
            baseline,
            repository_manifest(self.installed, value["sync"]["exclusions"]),
        )
