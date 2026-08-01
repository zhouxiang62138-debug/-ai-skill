import sqlite3
import tempfile
import unittest

from tests.runtime_test_support import make_store


class EventAppendOnlyTests(unittest.TestCase):
    def test_event_update_and_delete_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_event_append_") as directory:
            store, session_id = make_store(directory)
            connection = store.raw_connection()
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE events SET actor_id='changed' WHERE session_id=?",
                        (session_id,),
                    )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "DELETE FROM events WHERE session_id=?", (session_id,)
                    )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
