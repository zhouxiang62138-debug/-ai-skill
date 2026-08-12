import unittest

from scripts.project_migration import preview_migration, preview_runtime_migration
from scripts.project_state import validate_project_state
from tests.test_project_migration import v4_state


class V6ToV7PreviewTests(unittest.TestCase):
    def test_preview_preserves_governance_and_adds_runtime(self) -> None:
        state = preview_migration(v4_state())
        preview = preview_runtime_migration(state)
        self.assertEqual(state["iteration_sequence"], preview["iteration_sequence"])
        self.assertEqual(7, preview["schema_version"])
        self.assertIn("control_plane_id", preview["runtime"])
        self.assertNotIn("last_event_sequence", preview["runtime"])
        self.assertEqual([], validate_project_state(preview))


if __name__ == "__main__":
    unittest.main()
