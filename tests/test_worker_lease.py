import tempfile
import unittest
import multiprocessing
from datetime import datetime, timedelta, timezone

from runtime.errors import LeaseError
from runtime.leases import LeaseManager
from tests.runtime_test_support import make_store
from runtime.orchestrator import Orchestrator
from tests.runtime_test_support import make_runtime_project
from pathlib import Path


def _attempt_second_process_lease(database: str, session_id: str, queue: multiprocessing.Queue) -> None:
    """子进程独立连接 SQLite，验证不能共享有效 Lease。"""

    from runtime.session_store import SessionStore

    manager = LeaseManager(SessionStore(database))
    try:
        manager.acquire(session_id, "worker-child")
    except LeaseError:
        queue.put("REJECTED")
    else:  # pragma: no cover - 表示 fencing 失效
        queue.put("ACQUIRED")


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

    def test_two_processes_cannot_share_same_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_lease_process_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            lease = manager.acquire(session_id, "worker-parent")
            queue: multiprocessing.Queue = multiprocessing.Queue()
            process = multiprocessing.Process(
                target=_attempt_second_process_lease,
                args=(str(store.path), session_id, queue),
            )
            process.start()
            process.join(timeout=10)
            self.assertFalse(process.is_alive())
            self.assertEqual("REJECTED", queue.get(timeout=2))
            manager.release(session_id, "worker-parent", lease.lease_version, lease.lease_token or "")

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

    def test_hold_releases_lease_after_exception(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_lease_hold_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            with self.assertRaisesRegex(RuntimeError, "boom"):
                with manager.hold(session_id, "worker-a"):
                    raise RuntimeError("boom")
            with self.assertRaises(LeaseError):
                manager.get(session_id)

    def test_heartbeat_keeps_active_lease(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_lease_heartbeat_") as directory:
            store, session_id = make_store(directory)
            manager = LeaseManager(store)
            start = datetime.now(timezone.utc)
            lease = manager.acquire(session_id, "worker-a", ttl_seconds=2, now=start)
            renewed = manager.renew(
                session_id,
                "worker-a",
                lease.lease_version,
                lease.lease_token or "",
                ttl_seconds=10,
                now=start + timedelta(seconds=1),
            )
            self.assertEqual([], manager.detect_expired(now=start + timedelta(seconds=3)))
            manager.assert_valid(
                session_id, "worker-a", renewed.lease_version,
                renewed.lease_token or "", now=start + timedelta(seconds=3)
            )

    def test_default_workers_receive_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_worker_identity_") as directory:
            root, _ = make_runtime_project(directory)
            home = Path((root / ".test-control-plane-home").read_text(encoding="utf-8"))
            first = Orchestrator(root, control_plane_home=home).start()
            second_root, _ = make_runtime_project(Path(directory) / "second")
            second_home = Path((second_root / ".test-control-plane-home").read_text(encoding="utf-8"))
            second = Orchestrator(second_root, control_plane_home=second_home).start()
            self.assertNotEqual(first["worker_id"], second["worker_id"])

    def test_default_recovery_worker_receives_unique_id(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_recovery_worker_") as directory:
            root, session_id = make_runtime_project(directory)
            home = Path((root / ".test-control-plane-home").read_text(encoding="utf-8"))
            orchestrator = Orchestrator(root, control_plane_home=home)
            started = orchestrator.start()
            orchestrator.leases.release(
                session_id,
                started["worker_id"],
                started["lease_version"],
                started["lease_token"],
            )
            orchestrator.recover_session(session_id)
            with self.assertRaises(LeaseError):
                orchestrator.leases.get(session_id)


if __name__ == "__main__":
    unittest.main()
