import copy
import unittest

from scripts.project_migration import preview_runtime_migration
from scripts.project_state import validate_project_state
from tests.test_project_migration import v4_state


class LegacyProjectWithoutRuntimeTests(unittest.TestCase):
    def test_v3_to_v6_remain_valid_and_preview_is_read_only(self) -> None:
        for version in (3, 4, 5, 6):
            with self.subTest(version=version):
                state = v4_state()
                if version == 6:
                    from scripts.project_migration import preview_migration

                    state = preview_migration(state)
                else:
                    state["schema_version"] = version
                    if version == 3:
                        for field in (
                            "product_spec_status", "plan_status",
                            "plan_approval_status", "exploration_trigger_reasons",
                            "exploration_generation_attempt",
                            "design_feedback_status", "design_feedback_round",
                        ):
                            state.pop(field)
                before = copy.deepcopy(state)
                self.assertNotIn("runtime", state)
                preview_runtime_migration(state)
                self.assertEqual(before, state)


if __name__ == "__main__":
    unittest.main()
