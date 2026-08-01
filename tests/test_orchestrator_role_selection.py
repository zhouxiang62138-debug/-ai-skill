import tempfile
import unittest
from pathlib import Path

from runtime.orchestrator import Orchestrator
from scripts.project_state import load_project_state
from tests.runtime_test_support import make_runtime_project


class OrchestratorRoleSelectionTests(unittest.TestCase):
    def test_planning_selects_only_existing_planner_role(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_orchestrator_role_") as directory:
            root, session_id = make_runtime_project(directory)
            control_home = Path((root / ".test-control-plane-home").read_text())
            result = Orchestrator(root, control_plane_home=control_home).start()
            self.assertEqual(session_id, result["session_id"])
            self.assertEqual("ROLE", result["selection"].kind)
            self.assertEqual("planner", result["selection"].target)
            self.assertTrue(result["run_id"])

    def test_commit_step_commits_run_and_releases_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_orchestrator_commit_") as directory:
            root, session_id = make_runtime_project(directory)
            home = Path((root / ".test-control-plane-home").read_text())
            orchestrator = Orchestrator(root, control_plane_home=home)
            started = orchestrator.start()
            state = load_project_state(root / "project.yaml")
            result = orchestrator.commit_step(
                session_id, started["run_id"], started["lease_token"] or "",
                {
                    "source_status": state["status"],
                    "target_status": state["status"],
                    "changed_fields": {},
                    "expected_revision": 0,
                    "idempotency_key": "commit-step",
                },
            )
            self.assertEqual("COMMITTED", result["result"])


if __name__ == "__main__":
    unittest.main()
