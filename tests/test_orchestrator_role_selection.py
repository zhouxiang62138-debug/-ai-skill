import tempfile
import unittest
from pathlib import Path

from runtime.orchestrator import Orchestrator
from scripts.project_state import load_project_state
from scripts.project_state import serialize_project_state
from tests.runtime_test_support import commit_step_with_test_attestation, make_runtime_project


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
            result = commit_step_with_test_attestation(orchestrator,
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

    def test_fail_step_releases_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_orchestrator_fail_") as directory:
            root, session_id = make_runtime_project(directory)
            home = Path((root / ".test-control-plane-home").read_text())
            orchestrator = Orchestrator(root, control_plane_home=home)
            started = orchestrator.start()
            orchestrator.fail_step(session_id, started["run_id"], {"reason": "test"})
            self.assertEqual("FAILED", orchestrator.store.get_role_run(session_id, started["run_id"])["status"])
            with self.assertRaises(Exception):
                orchestrator.leases.get(session_id)

    def test_wait_state_does_not_hold_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_orchestrator_wait_") as directory:
            root, session_id = make_runtime_project(directory)
            state = load_project_state(root / "project.yaml")
            state["status"] = "WAITING_FOR_USER"
            state["next_role"] = None
            (root / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")
            home = Path((root / ".test-control-plane-home").read_text())
            result = Orchestrator(root, control_plane_home=home).start()
            self.assertEqual("WAIT", result["selection"].kind)
            self.assertIsNone(result["lease_token"])
            with self.assertRaises(Exception):
                Orchestrator(root, control_plane_home=home).leases.get(session_id)


if __name__ == "__main__":
    unittest.main()
