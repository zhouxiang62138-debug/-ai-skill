import sys
import tempfile
import unittest
from pathlib import Path

from runtime.environment import LocalWorkspaceEnvironment
from runtime.session_store import SessionStore


class ExecutionEnvironmentTests(unittest.TestCase):
    def test_local_environment_restricts_paths_and_records_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = SessionStore(root / ".runtime" / "sessions.sqlite3")
            session = store.create_session("p", root, idempotency_key="env")
            env = LocalWorkspaceEnvironment(root, session_id=session.session_id, store=store,
                allowed_prefixes=[[sys.executable, "-c"]])
            env.provision("p"); env.write_file("code/a.txt", "ok")
            self.assertEqual("ok", env.read_file("code/a.txt"))
            with self.assertRaises(Exception): env.write_file("../escape.txt", "no")
            result = env.execute([sys.executable, "-c", "print('ok')"])
            self.assertEqual(0, result.exit_code); self.assertIn("ok", result.stdout)
            self.assertTrue((root / result.result_reference).is_file())
            with self.assertRaises(Exception): env.execute(["not-allowed-command"])


if __name__ == "__main__": unittest.main()
