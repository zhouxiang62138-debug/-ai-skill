import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from runtime.errors import LeaseError
from runtime.leases import LeaseManager
from tests.runtime_test_support import make_store


class ExpiredLeaseRecoveryTests(unittest.TestCase):
    def test_expired_lease_can_be_stolen_and_fences_old_worker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_expired_lease_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            start = datetime.now(timezone.utc)
            old = manager.acquire(
                session_id, "worker-old", ttl_seconds=1, now=start
            )
            now = start + timedelta(seconds=2)
            self.assertEqual(1, len(manager.detect_expired(now=now)))
            new = manager.steal_expired(
                session_id, "worker-new", ttl_seconds=10, now=now
            )
            self.assertGreater(new.lease_version, old.lease_version)
            with self.assertRaises(LeaseError):
                manager.assert_valid(
                    session_id, "worker-old", old.lease_version, now=now
                )


if __name__ == "__main__":
    unittest.main()
