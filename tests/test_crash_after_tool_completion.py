import tempfile
import unittest

from runtime.session_store import SessionStore
from tests.runtime_test_support import make_runtime_project, open_runtime_store


class CrashAfterToolCompletionTests(unittest.TestCase):
    def test_completed_tool_result_reference_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_crash_after_tool_") as directory:
            root, session_id = make_runtime_project(directory)
            store = open_runtime_store(root)
            tool_call = store.request_tool_call(
                session_id,
                tool_name="test_runner",
                arguments={"suite": "unit"},
                idempotency_key="tool-1",
            )
            store.start_tool_call(session_id, tool_call)
            reference, digest = store.write_tool_result(
                tool_call, {"stdout": "ok", "stderr": "", "exit_code": 0}
            )
            store.complete_tool_call(
                session_id, tool_call, result_reference=reference, result_hash=digest
            )
            reopened = SessionStore(store.path)
            completed = reopened.completed_tool_calls(session_id)
            self.assertEqual(tool_call, completed[0]["tool_call_id"])
            self.assertEqual(
                reference, completed[0]["result_reference"]
            )


if __name__ == "__main__":
    unittest.main()
