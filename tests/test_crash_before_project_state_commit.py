import tempfile
import unittest

from runtime.leases import LeaseManager
from runtime.project_revision import ProjectStateCAS
from runtime.session_store import SessionStore
from scripts.project_state import load_project_state
from tests.runtime_test_support import make_runtime_project


class CrashBeforeProjectStateCommitTests(unittest.TestCase):
    def test_recovery_aborts_pending_without_changing_revision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_crash_before_state_") as directory:
            root, session_id = make_runtime_project(directory)
            store = SessionStore(root / ".runtime" / "sessions.sqlite3")
            leases = LeaseManager(store)
            lease = leases.acquire(session_id, "worker-a")
            cas = ProjectStateCAS(store, leases)
            state = load_project_state(root / "project.yaml")
            with self.assertRaises(OSError):
                cas.commit(
                    root / "project.yaml",
                    state,
                    session_id=session_id,
                    worker_id="worker-a",
                    lease_version=lease.lease_version,
                    expected_revision=0,
                    idempotency_key="crash-before",
                    fail_at="before_project_state_commit",
                )
            self.assertEqual(0, load_project_state(root / "project.yaml")["runtime"]["revision"])
            self.assertEqual(1, len(cas.recover_pending(root / "project.yaml", session_id)))
            self.assertEqual([], cas.recover_pending(root / "project.yaml", session_id))


if __name__ == "__main__":
    unittest.main()
