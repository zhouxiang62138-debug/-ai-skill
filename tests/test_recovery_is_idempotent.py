import tempfile
import unittest

from runtime.leases import LeaseManager
from runtime.project_revision import ProjectStateCAS
from runtime.recovery import RecoveryManager
from runtime.session_store import SessionStore
from tests.runtime_test_support import make_runtime_project


class RecoveryIdempotencyTests(unittest.TestCase):
    def test_repeated_recovery_does_not_duplicate_actions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_recovery_idempotent_") as directory:
            root, session_id = make_runtime_project(directory)
            store = SessionStore(root / ".runtime" / "sessions.sqlite3")
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
