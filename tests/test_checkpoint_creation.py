import tempfile
import unittest

from tests.runtime_test_support import make_store


class CheckpointCreationTests(unittest.TestCase):
    def test_checkpoint_contains_structured_recovery_facts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_checkpoint_") as directory:
            store, session_id = make_store(directory)
            checkpoint = store.create_checkpoint(
                session_id,
                project_revision=3,
                project_state_hash="a" * 64,
                active_role="generator",
                active_module=None,
                status="IMPLEMENTING",
                next_role="generator",
                open_transaction_ids=["tx-1"],
            )
            self.assertEqual(3, checkpoint.project_revision)
            self.assertEqual(("tx-1",), checkpoint.open_transaction_ids)
            self.assertEqual(checkpoint, store.get_checkpoint(checkpoint.checkpoint_id))


if __name__ == "__main__":
    unittest.main()
