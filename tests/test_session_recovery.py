import tempfile
import unittest

from runtime.orchestrator import Orchestrator
from tests.runtime_test_support import make_runtime_project


class SessionRecoveryTests(unittest.TestCase):
    def test_new_orchestrator_resumes_persisted_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_session_recovery_") as directory:
            root, session_id = make_runtime_project(directory)
            first = Orchestrator(root)
            first.start()
            first.pause(session_id)
            second = Orchestrator(root)
            inspected = second.resume(session_id)
            self.assertEqual("ACTIVE", inspected["session"].status)
            self.assertEqual("planner", inspected["selection"].target)


if __name__ == "__main__":
    unittest.main()
