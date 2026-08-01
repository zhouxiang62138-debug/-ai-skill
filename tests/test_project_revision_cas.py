import tempfile
import unittest
from pathlib import Path

from runtime.errors import RuntimeValidationError, StateConflictError
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
            result = cas.commit(
                project,
                next_state,
                session_id=session.session_id,
                worker_id="worker-a",
                actor_role="planner",
                lease_version=lease.lease_version,
                lease_token=lease.lease_token or "",
                expected_revision=0,
                idempotency_key="update-1",
            )
            self.assertEqual(1, result["revision"])
            self.assertEqual(1, load_project_state(project)["runtime"]["revision"])
            replay = cas.commit(
                project, next_state, session_id=session.session_id, worker_id="worker-a",
                actor_role="planner",
                lease_version=lease.lease_version, lease_token=lease.lease_token or "",
                expected_revision=0, idempotency_key="update-1",
            )
            self.assertEqual("IDEMPOTENT", replay["result"])
            with self.assertRaises(StateConflictError):
                cas.commit(
                    project,
                    next_state,
                    session_id=session.session_id,
                worker_id="worker-a",
                actor_role="planner",
                lease_version=lease.lease_version,
                lease_token=lease.lease_token or "",
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

    def test_cas_rejects_role_unowned_field(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_cas_ownership_") as directory:
            root = Path(directory)
            store = SessionStore(root / "sessions.sqlite3")
            session = store.create_session("test_migration", root, idempotency_key="ownership")
            project = root / "project.yaml"
            state = preview_runtime_migration(v4_state(), project_root=root, session_id=session.session_id)
            project.write_text(serialize_project_state(state), encoding="utf-8")
            lease = LeaseManager(store).acquire(session.session_id, "worker-a")
            candidate = dict(state)
            candidate["current_iteration"] = 1
            with self.assertRaises(RuntimeValidationError):
                ProjectStateCAS(store, LeaseManager(store)).commit(
                    project, candidate, session_id=session.session_id, worker_id="worker-a",
                    actor_role="planner", lease_version=lease.lease_version,
                    lease_token=lease.lease_token or "", expected_revision=0,
                    idempotency_key="unowned",
                )


if __name__ == "__main__":
    unittest.main()
