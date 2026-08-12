import tempfile
import unittest

from runtime.event_types import ActorType, EventType
from tests.runtime_test_support import make_store


class EventSequenceTests(unittest.TestCase):
    def test_sequence_is_contiguous(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_event_sequence_") as directory:
            store, session_id = make_store(directory)
            for index in range(2):
                store.append_event(
                    session_id,
                    EventType.PROJECT_STATE_READ,
                    ActorType.ORCHESTRATOR,
                    "orchestrator",
                    idempotency_key=f"read:{index}",
                    correlation_id=session_id,
                    payload={"index": index},
                )
            self.assertEqual([1, 2, 3], [e.sequence for e in store.list_events(session_id)])


if __name__ == "__main__":
    unittest.main()
