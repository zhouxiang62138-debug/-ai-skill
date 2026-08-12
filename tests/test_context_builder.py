import tempfile
import unittest
from pathlib import Path

from experimental.f13_context_builder.context_builder import build_context
from runtime.errors import RuntimeValidationError
from runtime.session_store import SessionStore


class ContextBuilderTests(unittest.TestCase):
    def test_generator_cannot_read_unapproved_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = SessionStore(root / ".runtime/sessions.sqlite3")
            session = store.create_session("p", root, idempotency_key="context")
            target = root / "memory/proposals/a.md"; target.parent.mkdir(parents=True); target.write_text("secret plan", encoding="utf-8")
            with self.assertRaises(RuntimeValidationError):
                build_context(session.session_id, "generator", None, ["memory/proposals/a.md"], [], 10, project_root=root, store=store)
            result = build_context(session.session_id, "planner", None, ["memory/proposals/a.md"], ["ISSUE-1"], 10, project_root=root, store=store)
            self.assertEqual("planner", result["role"])


if __name__ == "__main__": unittest.main()
