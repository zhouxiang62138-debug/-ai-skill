import tempfile
import unittest
import json
from unittest.mock import patch
from pathlib import Path

from scripts.project_migration import (
    migrate_project_to_v7,
    rollback_project_file,
)
from scripts.project_state import (
    load_project_state,
    serialize_project_state,
)
from runtime.control_plane import session_database_path
from runtime.session_store import SessionStore
from tests.test_project_migration import v4_state


class RuntimeMigrationRollbackTests(unittest.TestCase):
    def test_migration_failure_before_control_plane_is_recoverable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_v7_migration_failure_") as directory:
            root = Path(directory)
            project = root / "project.yaml"
            project.write_text(serialize_project_state(v4_state()), encoding="utf-8")
            backup = root / "backups" / "project-v4.yaml"
            with patch("runtime.control_plane.initialize_control_plane", side_effect=OSError("control plane unavailable")):
                with self.assertRaisesRegex(OSError, "control plane unavailable"):
                    migrate_project_to_v7(project, backup, control_plane_home=root / "control-home")
            self.assertEqual(4, load_project_state(project)["schema_version"])
            records = sorted((root / "memory" / "migrations").glob("migration-*.json"))
            self.assertEqual(1, len(records))
            self.assertEqual("RECOVERY_REQUIRED", json.loads(records[0].read_text(encoding="utf-8"))["status"])

    def test_migration_failure_after_control_plane_is_recoverable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_v7_migration_session_failure_") as directory:
            root = Path(directory)
            project = root / "project.yaml"
            project.write_text(serialize_project_state(v4_state()), encoding="utf-8")
            backup = root / "backups" / "project-v4.yaml"
            with patch("runtime.session_store.SessionStore.create_session", side_effect=OSError("session initialization failed")):
                with self.assertRaisesRegex(OSError, "session initialization failed"):
                    migrate_project_to_v7(project, backup, control_plane_home=root / "control-home")
            self.assertEqual(4, load_project_state(project)["schema_version"])
            record = json.loads(next((root / "memory" / "migrations").glob("migration-*.json")).read_text(encoding="utf-8"))
            self.assertEqual("RECOVERY_REQUIRED", record["status"])
            self.assertTrue((root / "control-home").exists())

    def test_v7_migration_can_restore_original_v4(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_v7_rollback_") as directory:
            root = Path(directory)
            project = root / "project.yaml"
            project.write_text(
                serialize_project_state(v4_state()), encoding="utf-8"
            )
            backup = root / "backups" / "project-v4.yaml"
            result = migrate_project_to_v7(
                project, backup, control_plane_home=root / "control-home"
            )
            self.assertEqual(7, load_project_state(project)["schema_version"])
            self.assertTrue(result["verification"]["valid"])
            record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
            self.assertEqual("COMMITTED", record["status"])
            before_rollback = root / "backups" / "project-v7.yaml"
            rollback = rollback_project_file(
                project, backup, before_rollback, control_plane_home=root / "control-home"
            )
            self.assertEqual(4, load_project_state(project)["schema_version"])
            self.assertTrue(before_rollback.is_file())
            self.assertEqual(
                "ROLLED_BACK",
                json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))["status"],
            )
            store = SessionStore(session_database_path("test_migration", home=root / "control-home"))
            self.assertEqual("DETACHED", store.get_session(rollback["detached_session_id"]).status)


if __name__ == "__main__":
    unittest.main()
