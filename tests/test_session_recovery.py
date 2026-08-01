import tempfile
import unittest
from pathlib import Path

from runtime.orchestrator import Orchestrator
from tests.runtime_test_support import make_runtime_project


class SessionRecoveryTests(unittest.TestCase):
    def test_new_orchestrator_resumes_persisted_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_session_recovery_") as directory:
            root, session_id = make_runtime_project(directory)
            control_home = Path((root / ".test-control-plane-home").read_text())
            first = Orchestrator(root, control_plane_home=control_home)
            first.start()
            first.pause(session_id)
            with self.assertRaises(Exception):
                first.leases.get(session_id)
            second = Orchestrator(root, control_plane_home=control_home)
            inspected = second.resume(session_id)
            self.assertEqual("ACTIVE", second.inspect(session_id)["session"].status)
            self.assertEqual("planner", inspected["selection"].target)
            self.assertIsNotNone(inspected["lease_token"])


if __name__ == "__main__":
    unittest.main()
