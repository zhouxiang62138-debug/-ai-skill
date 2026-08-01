import tempfile
import unittest

from runtime.leases import LeaseManager
from runtime.project_revision import ProjectStateCAS
from scripts.project_state import load_project_state
from tests.runtime_test_support import make_runtime_project, open_runtime_store


class CrashAfterProjectStateCommitTests(unittest.TestCase):
    def test_recovery_marks_matching_pending_revision_committed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_crash_after_state_") as directory:
            root, session_id = make_runtime_project(directory)
            store = open_runtime_store(root)
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
                    actor_role="runtime_orchestrator",
                    lease_version=lease.lease_version,
                    lease_token=lease.lease_token or "",
                    expected_revision=0,
                    idempotency_key="crash-after",
                    fail_at="after_project_state_commit",
                )
            self.assertEqual(1, load_project_state(root / "project.yaml")["runtime"]["revision"])
            actions = cas.recover_pending(root / "project.yaml", session_id)
            self.assertTrue(actions[0].startswith("committed:"))


if __name__ == "__main__":
    unittest.main()
