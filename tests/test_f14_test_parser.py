import tempfile
import unittest

from runtime.deterministic.store import DerivedRuntimeStore
from runtime.deterministic.test_parser import TestOutputParser
from tests.runtime_test_support import make_store


class F14TestOutputParserTests(unittest.TestCase):
    def test_structured_runtime_result_has_priority(self) -> None:
        parser = TestOutputParser()
        result = parser.parse(
            {
                "tests": {
                    "total": 3,
                    "passed": 2,
                    "failed": 1,
                    "skipped": 0,
                    "error": 0,
                },
                "failed_tests": ["test_a"],
                "coverage_metrics": {"line": 88.0},
            },
            command="pytest",
            exit_code=1,
            source_locator="runtime:test-result",
            raw_log_locator="logs/test.log",
        )
        self.assertEqual("PARSED", result.parse_status)
        self.assertEqual("structured", result.confidence)
        self.assertEqual(("test_a",), result.failed_tests)

    def test_junit_is_used_before_console_fallback_and_can_persist(self) -> None:
        xml = """
        <testsuite tests="2" failures="1" errors="0" skipped="0">
          <testcase classname="suite" name="ok" time="0.01"/>
          <testcase classname="suite" name="bad" time="0.02">
            <failure message="failed"/>
          </testcase>
        </testsuite>
        """
        parser = TestOutputParser()
        result = parser.parse(
            xml,
            command="pytest --junitxml=result.xml",
            exit_code=1,
            source_locator="result.xml",
            raw_log_locator="logs/test.log",
        )
        self.assertEqual("machine_readable", result.confidence)
        self.assertEqual(1, result.tests["failed"])
        with tempfile.TemporaryDirectory(prefix="test_f14_parser_") as directory:
            store, session_id = make_store(directory)
            result_id = parser.persist(store, session_id=session_id, result=result)
            loaded = DerivedRuntimeStore(store).read_test_result(session_id, result_id)
            self.assertEqual("PARSED", loaded["result"]["parse_status"])
            self.assertEqual("logs/test.log", loaded["result"]["raw_log_locator"])

    def test_structured_pytest_and_console_fallback(self) -> None:
        parser = TestOutputParser()
        structured = parser.parse(
            {"total": 4, "passed": 4, "failed": 0, "skipped": 0, "error": 0},
            command="pytest",
            exit_code=0,
            source_locator="pytest:structured",
            raw_log_locator="logs/test.log",
        )
        self.assertEqual("structured", structured.confidence)
        metrics = parser.parse(
            {"test_metrics": {"total": 2, "passed": 2}},
            command="pytest",
            exit_code=0,
            source_locator="pytest:metrics",
            raw_log_locator="logs/test.log",
        )
        self.assertEqual(2, metrics.tests["total"])
        fallback = parser.parse(
            "2 passed, 1 failed, 1 skipped in 0.20s",
            command="pytest",
            exit_code=1,
            source_locator="console",
            raw_log_locator="logs/test.log",
        )
        self.assertEqual("console_fallback", fallback.confidence)
        self.assertEqual(4, fallback.tests["total"])

    def test_ambiguous_or_malformed_output_is_unknown(self) -> None:
        parser = TestOutputParser()
        malformed = parser.parse(
            "<testsuite><testcase>",
            command="pytest",
            exit_code=0,
            source_locator="broken.xml",
            raw_log_locator="logs/test.log",
        )
        self.assertEqual("UNKNOWN", malformed.parse_status)
        self.assertEqual("PARSE_AMBIGUOUS", malformed.reason)
        self.assertFalse(malformed.tests["total"] > 0)
        missing_locator = parser.parse(
            {"tests": {"total": 1, "passed": 1}},
            command="pytest",
            exit_code=0,
            source_locator="structured",
        )
        self.assertEqual("UNKNOWN", missing_locator.parse_status)
        self.assertEqual("RAW_LOG_LOCATOR_MISSING", missing_locator.reason)

    def test_result_corruption_is_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_parser_corrupt_") as directory:
            store, session_id = make_store(directory)
            parser = TestOutputParser()
            result = parser.parse(
                {"tests": {"total": 1, "passed": 1}},
                command="pytest",
                exit_code=0,
                source_locator="structured",
                raw_log_locator="logs/test.log",
            )
            result_id = parser.persist(store, session_id=session_id, result=result)
            connection = store.raw_connection()
            try:
                connection.execute("DROP TRIGGER f14_test_results_no_update")
                connection.execute(
                    "UPDATE f14_test_results SET result_json=? WHERE result_id=?",
                    ("{\"parse_status\":\"PARSED\"}", result_id),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(Exception, "TEST_RESULT_CORRUPT"):
                DerivedRuntimeStore(store).read_test_result(session_id, result_id)


if __name__ == "__main__":
    unittest.main()
