import tempfile
import unittest

from runtime.orchestrator import Orchestrator
from tests.runtime_test_support import make_runtime_project


class OrchestratorRoleSelectionTests(unittest.TestCase):
    def test_planning_selects_only_existing_planner_role(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_orchestrator_role_") as directory:
            root, session_id = make_runtime_project(directory)
            result = Orchestrator(root).start()
            self.assertEqual(session_id, result["session_id"])
            self.assertEqual("ROLE", result["selection"].kind)
            self.assertEqual("planner", result["selection"].target)


if __name__ == "__main__":
    unittest.main()
