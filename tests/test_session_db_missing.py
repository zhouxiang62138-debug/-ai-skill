import tempfile
import unittest
from pathlib import Path

from runtime.recovery import session_database_missing


class SessionDatabaseMissingTests(unittest.TestCase):
    def test_project_without_runtime_database_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_session_db_missing_") as directory:
            root = Path(directory)
            (root / "project.yaml").write_text("schema_version: 6\n", encoding="utf-8")
            self.assertTrue(session_database_missing(root))


if __name__ == "__main__":
    unittest.main()
