import tempfile
import unittest

from runtime.leases import LeaseManager
from runtime.project_revision import ProjectStateCAS
from runtime.recovery import RecoveryManager
from tests.runtime_test_support import make_runtime_project, open_runtime_store


class RecoveryIdempotencyTests(unittest.TestCase):
    def test_repeated_recovery_does_not_duplicate_actions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_recovery_idempotent_") as directory:
            root, session_id = make_runtime_project(directory)
            store = open_runtime_store(root)
            manager = RecoveryManager(
                store, ProjectStateCAS(store, LeaseManager(store))
            )
            first = manager.recover(session_id)
            event_count = len(store.list_events(session_id))
            second = manager.recover(session_id)
            self.assertEqual(first, second)
            self.assertEqual(event_count, len(store.list_events(session_id)))


if __name__ == "__main__":
    unittest.main()
