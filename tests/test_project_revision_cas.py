import tempfile
import unittest
from pathlib import Path

from runtime.errors import StateConflictError
from runtime.leases import LeaseManager
from runtime.project_revision import ProjectStateCAS
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state
from scripts.project_state import ProjectStateError, write_project_state_atomic
from tests.test_project_migration import v4_state


class ProjectRevisionCASTests(unittest.TestCase):
    def test_cas_commits_once_and_rejects_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_project_cas_") as directory:
            root = Path(directory)
            store = SessionStore(root / ".runtime" / "sessions.sqlite3")
            session = store.create_session(
                "test_migration", root, idempotency_key="cas-session"
            )
            project = root / "project.yaml"
            state = preview_runtime_migration(
                v4_state(), project_root=root, session_id=session.session_id
            )
            project.write_text(serialize_project_state(state), encoding="utf-8")
            leases = LeaseManager(store)
            lease = leases.acquire(session.session_id, "worker-a")
            cas = ProjectStateCAS(store, leases)
            next_state = dict(state)
            next_state["blocked_reason"] = "test-update"
            result = cas.commit(
                project,
                next_state,
                session_id=session.session_id,
                worker_id="worker-a",
                lease_version=lease.lease_version,
                expected_revision=0,
                idempotency_key="update-1",
            )
            self.assertEqual(1, result["revision"])
            self.assertEqual(1, load_project_state(project)["runtime"]["revision"])
            with self.assertRaises(StateConflictError):
                cas.commit(
                    project,
                    next_state,
                    session_id=session.session_id,
                    worker_id="worker-a",
                    lease_version=lease.lease_version,
                    expected_revision=0,
                    idempotency_key="stale",
                )

    def test_direct_v7_writer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_direct_v7_writer_") as directory:
            root = Path(directory)
            store = SessionStore(root / ".runtime" / "sessions.sqlite3")
            session = store.create_session(
                "test_migration", root, idempotency_key="direct-writer-session"
            )
            project = root / "project.yaml"
            state = preview_runtime_migration(
                v4_state(), project_root=root, session_id=session.session_id
            )
            project.write_text(serialize_project_state(state), encoding="utf-8")
            with self.assertRaises(ProjectStateError):
                write_project_state_atomic(project, state)


if __name__ == "__main__":
    unittest.main()
