import tempfile
import unittest

from runtime.errors import RuntimeValidationError
from runtime.event_types import ActorType, EventType, MAX_EVENT_PAYLOAD_BYTES
from tests.runtime_test_support import make_store


class EventPayloadLimitsTests(unittest.TestCase):
    def test_payload_limits_and_secret_like_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_event_payload_") as directory:
            store, session_id = make_store(directory)
            with self.assertRaises(RuntimeValidationError):
                store.append_event(
                    session_id, EventType.PROJECT_STATE_READ, ActorType.SYSTEM, "test",
                    idempotency_key="oversized", correlation_id=session_id,
                    payload={"text": "x" * (MAX_EVENT_PAYLOAD_BYTES + 1)},
                )
            with self.assertRaises(RuntimeValidationError):
                store.append_event(
                    session_id, EventType.PROJECT_STATE_READ, ActorType.SYSTEM, "test",
                    idempotency_key="secret", correlation_id=session_id,
                    payload={"api_key": "not-allowed"},
                )


if __name__ == "__main__":
    unittest.main()
