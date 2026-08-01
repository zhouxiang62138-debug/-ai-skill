from pathlib import Path

import pytest

from runtime.errors import RuntimeStorageError
from runtime.session_store import SessionStore


def test_tool_result_is_immutable_and_hash_checked(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "control" / "sessions.sqlite3")
    reference, digest = store.write_tool_result("tool-a", {"stdout": "ok", "exit_code": 0})

    assert reference.startswith("tool-results/tool-a/")
    assert store.read_tool_result(reference, digest)["stdout"] == "ok"
    (store.path.parent / reference).write_text('{"stdout":"changed"}', encoding="utf-8")
    with pytest.raises(RuntimeStorageError, match="TOOL_RESULT_HASH_MISMATCH"):
        store.read_tool_result(reference, digest)
