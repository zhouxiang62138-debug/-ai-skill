import tempfile
import unittest

from runtime.errors import RuntimeValidationError
from runtime.event_types import ActorType, EventType
from tests.runtime_test_support import make_store


class EventIdempotencyTests(unittest.TestCase):
    def test_replay_returns_same_event_and_conflicting_replay_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_event_idempotency_") as directory:
            store, session_id = make_store(directory)
            kwargs = {
                "session_id": session_id,
                "event_type": EventType.PROJECT_STATE_READ,
                "actor_type": ActorType.ORCHESTRATOR,
                "actor_id": "orchestrator",
                "idempotency_key": "same",
                "correlation_id": session_id,
                "payload": {"revision": 0},
            }
            first = store.append_event(**kwargs)
            second = store.append_event(**kwargs)
            self.assertEqual(first, second)
            kwargs["payload"] = {"revision": 1}
            with self.assertRaises(RuntimeValidationError):
                store.append_event(**kwargs)


if __name__ == "__main__":
    unittest.main()
