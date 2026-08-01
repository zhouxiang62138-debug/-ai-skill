import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from runtime.errors import LeaseError
from runtime.leases import LeaseManager
from tests.runtime_test_support import make_store


class WorkerLeaseTests(unittest.TestCase):
    def test_only_current_version_holder_can_commit_or_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_worker_lease_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            lease = manager.acquire(session_id, "worker-a")
            manager.assert_valid(session_id, "worker-a", lease.lease_version)
            with self.assertRaises(LeaseError):
                manager.acquire(session_id, "worker-b")
            with self.assertRaises(LeaseError):
                manager.release(session_id, "worker-a", lease.lease_version + 1)
            manager.release(session_id, "worker-a", lease.lease_version)


if __name__ == "__main__":
    unittest.main()
