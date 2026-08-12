import tempfile
import unittest

from tests.runtime_test_support import make_store


class SessionStoreTests(unittest.TestCase):
    def test_session_is_durable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_session_store_") as directory:
            store, session_id = make_store(directory)
            reopened = type(store)(store.path)
            session = reopened.get_session(session_id)
            self.assertEqual("test_runtime", session.project_id)
            duplicate = reopened.create_session(
                "test_runtime", session.project_root, idempotency_key="test-session"
            )
            self.assertEqual(session_id, duplicate.session_id)
            self.assertEqual(1, len(reopened.list_events(session_id)))


if __name__ == "__main__":
    unittest.main()
