import unittest

from scripts.project_migration import preview_runtime_migration
from scripts.project_state import validate_project_state
from tests.test_project_migration import v3_state


class V3ToV7PreviewTests(unittest.TestCase):
    def test_preview_is_valid_v7(self) -> None:
        preview = preview_runtime_migration(v3_state())
        self.assertEqual(7, preview["schema_version"])
        self.assertEqual(0, preview["runtime"]["revision"])
        self.assertEqual([], validate_project_state(preview))


if __name__ == "__main__":
    unittest.main()
