import tempfile
import unittest
from pathlib import Path

from scripts.project_migration import (
    migrate_project_to_v7,
    rollback_project_file,
)
from scripts.project_state import (
    load_project_state,
    serialize_project_state,
)
from tests.test_project_migration import v4_state


class RuntimeMigrationRollbackTests(unittest.TestCase):
    def test_v7_migration_can_restore_original_v4(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_v7_rollback_") as directory:
            root = Path(directory)
            project = root / "project.yaml"
            project.write_text(
                serialize_project_state(v4_state()), encoding="utf-8"
            )
            backup = root / "backups" / "project-v4.yaml"
            result = migrate_project_to_v7(project, backup)
            self.assertEqual(7, load_project_state(project)["schema_version"])
            self.assertTrue(result["verification"]["valid"])
            before_rollback = root / "backups" / "project-v7.yaml"
            rollback_project_file(project, backup, before_rollback)
            self.assertEqual(4, load_project_state(project)["schema_version"])
            self.assertTrue(before_rollback.is_file())


if __name__ == "__main__":
    unittest.main()
