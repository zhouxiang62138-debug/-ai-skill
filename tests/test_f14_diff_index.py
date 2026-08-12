import tempfile
import unittest

from runtime.deterministic.diff_index import DiffIndexBuilder
from runtime.deterministic.store import DerivedRuntimeStore
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from tests.runtime_test_support import make_store


class F14DiffIndexTests(unittest.TestCase):
    def test_diff_is_deterministic_and_persisted(self) -> None:
        result = DiffIndexBuilder.build(
            baseline_revision=2,
            current_revision=3,
            baseline_files={"code/a.py": "a", "code/old.py": "old"},
            current_files={"code/a.py": "a2", "code/new.py": "new"},
            baseline_artifacts={"plan": "hash-old"},
            current_artifacts={"plan": "hash-new", "evidence": "hash-e"},
            explicitly_affected_nodes=("AC-007",),
            unknown_impact_nodes=("AC-008",),
            expected_current_revision=3,
        )
        self.assertEqual(["code/a.py", "code/new.py", "code/old.py"], list(result.changed_files))
        self.assertEqual(["evidence", "plan"], list(result.changed_artifacts))
        self.assertEqual(("AC-007",), result.explicitly_affected_nodes)
        self.assertEqual(("AC-008",), result.unknown_impact_nodes)
        with tempfile.TemporaryDirectory(prefix="test_f14_diff_") as directory:
            store, session_id = make_store(directory)
            diff_id = result.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
            )
            loaded = DerivedRuntimeStore(store).read_diff_index(session_id, diff_id)
            self.assertEqual(3, loaded["current_revision"])
            self.assertEqual("a2", loaded["result"]["changed_hashes"]["code/a.py"]["after"])

    def test_diff_fails_closed_for_missing_baseline_or_revision_mismatch(self) -> None:
        with self.assertRaisesRegex(RuntimeValidationError, "BASELINE_MISSING"):
            DiffIndexBuilder.build(
                baseline_revision=1,
                current_revision=2,
                baseline_files=None,
                current_files={},
            )
        with self.assertRaisesRegex(RuntimeValidationError, "CURRENT_REVISION_MISMATCH"):
            DiffIndexBuilder.build(
                baseline_revision=1,
                current_revision=2,
                baseline_files={},
                current_files={},
                expected_current_revision=3,
            )
        with self.assertRaisesRegex(RuntimeValidationError, "LOCATOR_ESCAPE"):
            DiffIndexBuilder.build(
                baseline_revision=1,
                current_revision=2,
                baseline_files={"../secret": "a"},
                current_files={},
            )

    def test_diff_corruption_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_diff_corrupt_") as directory:
            store, session_id = make_store(directory)
            result = DiffIndexBuilder.build(
                baseline_revision=1,
                current_revision=2,
                baseline_files={"a": "old"},
                current_files={"a": "new"},
            )
            diff_id = result.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
            )
            connection = store.raw_connection()
            try:
                connection.execute("DROP TRIGGER f14_diff_indexes_no_update")
                connection.execute(
                    "UPDATE f14_diff_indexes SET result_json=? WHERE diff_id=?",
                    ("{\"changed_files\": [\"corrupted\"]}", diff_id),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeStorageError, "DIFF_CORRUPT"):
                DerivedRuntimeStore(store).read_diff_index(session_id, diff_id)


if __name__ == "__main__":
    unittest.main()
