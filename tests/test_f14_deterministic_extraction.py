import json
import tempfile
import unittest

from runtime.deterministic.telemetry import RuntimeTelemetry
from tests.runtime_test_support import make_store


class F14MinimalTelemetryTests(unittest.TestCase):
    def test_schema_is_additive_and_metrics_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_telemetry_") as directory:
            store, session_id = make_store(directory)
            telemetry = RuntimeTelemetry()
            telemetry.record_file_read(12)
            telemetry.record_hash()
            telemetry.record_directory_scan()
            telemetry.record_parser_run()
            telemetry.record_context(3, 128)
            telemetry.record_invocation("python_only")
            telemetry.record_invocation("llm", real_llm_invocation=True)
            self.assertTrue(
                telemetry.persist(
                    store,
                    session_id=session_id,
                    project_id="test_runtime",
                    project_revision=2,
                    role="generator",
                    phase="main",
                    execution_type="llm",
                    idempotency_key="telemetry-1",
                )
            )
            connection = store.raw_connection()
            try:
                row = connection.execute(
                    "SELECT * FROM f14_telemetry WHERE session_id=?",
                    (session_id,),
                ).fetchone()
                schema = connection.execute(
                    "SELECT MAX(version) AS version FROM runtime_schema"
                ).fetchone()
            finally:
                connection.close()
            self.assertIsNotNone(row)
            self.assertGreaterEqual(int(schema["version"]), 7)
            self.assertEqual(1, row["schema_version"])
            self.assertEqual("llm", row["execution_type"])
            self.assertEqual(
                1,
                json.loads(row["runtime_json"])["runtime_efficiency"]["file_reads"],
            )
            self.assertEqual(1, json.loads(row["model_json"])["model_efficiency"]["real_llm_invocations"])
            self.assertEqual(
                "unavailable",
                json.loads(row["model_json"])["model_efficiency"]["input_tokens"],
            )

    def test_lifecycle_records_are_not_real_llm_count(self) -> None:
        telemetry = RuntimeTelemetry()
        telemetry.record_invocation("python_only")
        telemetry.record_invocation("reused_deterministic")
        telemetry.record_invocation("rollover")
        self.assertEqual(0, telemetry.to_dict()["model_efficiency"]["real_llm_invocations"])
        telemetry.record_invocation("perception", real_llm_invocation=True)
        self.assertEqual(1, telemetry.to_dict()["model_efficiency"]["real_llm_invocations"])

    def test_persist_is_idempotent_and_telemetry_failure_is_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_telemetry_idempotent_") as directory:
            store, session_id = make_store(directory)
            telemetry = RuntimeTelemetry()
            kwargs = {
                "session_id": session_id,
                "project_id": "test_runtime",
                "idempotency_key": "same-key",
            }
            self.assertTrue(telemetry.persist(store, **kwargs))
            self.assertTrue(telemetry.persist(store, **kwargs))
            connection = store.raw_connection()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM f14_telemetry WHERE session_id=?",
                    (session_id,),
                ).fetchone()["count"]
            finally:
                connection.close()
            self.assertEqual(1, count)

            class BrokenStore:
                def transaction(self, **_kwargs):
                    raise OSError("telemetry storage unavailable")

            self.assertFalse(telemetry.persist(BrokenStore(), **kwargs))


if __name__ == "__main__":
    unittest.main()
