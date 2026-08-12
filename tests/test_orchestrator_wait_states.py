import unittest

from runtime.role_selector import select_role


class OrchestratorWaitStateTests(unittest.TestCase):
    def test_wait_states_never_launch_role(self) -> None:
        for status in (
            "WAITING_FOR_REQUIREMENTS",
            "WAITING_FOR_DESIGN_REVIEW",
            "WAITING_FOR_PRODUCT_REVIEW",
            "WAITING_FOR_PLAN_REVIEW",
            "WAITING_FOR_CHANGE_APPROVAL",
            "WAITING_FOR_USER",
            "BLOCKED",
            "ACCEPTED",
            "ARCHIVED",
        ):
            with self.subTest(status=status):
                selection = select_role(
                    {"status": status, "next_role": "planner", "active_module": None}
                )
                self.assertEqual("WAIT", selection.kind)
                self.assertIsNone(selection.target)


if __name__ == "__main__":
    unittest.main()
