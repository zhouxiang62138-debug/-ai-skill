import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from runtime.errors import LeaseError
from runtime.leases import LeaseManager
from tests.runtime_test_support import make_store
from runtime.orchestrator import Orchestrator
from tests.runtime_test_support import make_runtime_project
from pathlib import Path


class WorkerLeaseTests(unittest.TestCase):
    def test_only_current_version_holder_can_commit_or_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_worker_lease_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            lease = manager.acquire(session_id, "worker-a")
            self.assertIsNotNone(lease.lease_token)
            manager.assert_valid(session_id, "worker-a", lease.lease_version, lease.lease_token or "")
            with self.assertRaises(LeaseError):
                manager.acquire(session_id, "worker-b")
            with self.assertRaises(LeaseError):
                manager.release(session_id, "worker-a", lease.lease_version + 1, lease.lease_token or "")
            manager.release(session_id, "worker-a", lease.lease_version, lease.lease_token or "")

    def test_lease_token_is_hashed_and_required(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_worker_token_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            lease = manager.acquire(session_id, "worker-a")
            self.assertTrue(lease.lease_token)
            connection = store.raw_connection()
            try:
                stored = connection.execute(
                    "SELECT lease_token_hash FROM leases WHERE session_id=?", (session_id,)
                ).fetchone()["lease_token_hash"]
            finally:
                connection.close()
            self.assertNotEqual(lease.lease_token, stored)
            with self.assertRaises(LeaseError):
                manager.assert_valid(session_id, "worker-a", lease.lease_version, "wrong-token")

    def test_default_workers_receive_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_worker_identity_") as directory:
            root, _ = make_runtime_project(directory)
            home = Path((root / ".test-control-plane-home").read_text(encoding="utf-8"))
            first = Orchestrator(root, control_plane_home=home).start()
            second_root, _ = make_runtime_project(Path(directory) / "second")
            second_home = Path((second_root / ".test-control-plane-home").read_text(encoding="utf-8"))
            second = Orchestrator(second_root, control_plane_home=second_home).start()
            self.assertNotEqual(first["worker_id"], second["worker_id"])


if __name__ == "__main__":
    unittest.main()
