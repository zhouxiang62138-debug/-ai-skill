"""F14-B 结构化优先的 Test Output Parser。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..errors import RuntimeValidationError
from ..session_store import SessionStore
from .store import DerivedRuntimeStore
from .telemetry import RuntimeTelemetry
from .source_cache import SourceCache, SourceReadResult


PARSER_VERSION = "f14-test-parser-v1"
_COUNT_NAMES = ("total", "passed", "failed", "skipped", "error")


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


@dataclass(frozen=True)
class ParsedTestResult:
    parse_status: str
    parser_version: str
    command: str
    exit_code: int | None
    duration_ms: int | None
    tests: Mapping[str, int]
    failed_tests: tuple[str, ...]
    coverage_metrics: Mapping[str, Any]
    source_locator: str
    raw_log_locator: str
    confidence: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["tests"] = dict(self.tests)
        value["coverage_metrics"] = dict(self.coverage_metrics)
        value["failed_tests"] = list(self.failed_tests)
        return value


class TestOutputParser:
    __test__ = False

    """按确定性输入优先级解析，不会把未知结果标成 passed。"""

    def __init__(
        self,
        *,
        parser_version: str = PARSER_VERSION,
        telemetry: RuntimeTelemetry | None = None,
        source_cache: SourceCache | None = None,
    ) -> None:
        self.parser_version = parser_version
        self.telemetry = telemetry or RuntimeTelemetry()
        self.source_cache = source_cache

    def _finish(
        self,
        result: ParsedTestResult,
        source_read: SourceReadResult | None,
    ) -> ParsedTestResult:
        if source_read is not None and result.parse_status == "PARSED":
            self.source_cache.put_parsed(
                source_read,
                parsed=result.to_dict(),
                derived={"source_locator": result.source_locator},
            )
        return result

    def _unknown(
        self,
        *,
        command: str,
        exit_code: int | None,
        duration_ms: int | None,
        source_locator: str,
        raw_log_locator: str,
        reason: str,
    ) -> ParsedTestResult:
        return ParsedTestResult(
            parse_status="UNKNOWN",
            parser_version=self.parser_version,
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            tests={name: 0 for name in _COUNT_NAMES},
            failed_tests=(),
            coverage_metrics={},
            source_locator=source_locator,
            raw_log_locator=raw_log_locator,
            confidence="none",
            reason=reason,
        )

    def _validate_context(
        self,
        *,
        command: str,
        exit_code: int | None,
        duration_ms: int | None,
        source_locator: str,
        raw_log_locator: str,
    ) -> ParsedTestResult | None:
        if not isinstance(command, str) or not command:
            return self._unknown(
                command="",
                exit_code=exit_code,
                duration_ms=duration_ms,
                source_locator=source_locator,
                raw_log_locator=raw_log_locator,
                reason="COMMAND_MISSING",
            )
        if not isinstance(raw_log_locator, str) or not raw_log_locator:
            return self._unknown(
                command=command,
                exit_code=exit_code,
                duration_ms=duration_ms,
                source_locator=source_locator,
                raw_log_locator="",
                reason="RAW_LOG_LOCATOR_MISSING",
            )
        if exit_code is not None and not isinstance(exit_code, int):
            return self._unknown(
                command=command,
                exit_code=None,
                duration_ms=duration_ms,
                source_locator=source_locator,
                raw_log_locator=raw_log_locator,
                reason="EXIT_CODE_INVALID",
            )
        return None

    @staticmethod
    def _counts(value: Mapping[str, Any]) -> tuple[dict[str, int], list[str]] | None:
        raw_tests = value.get("tests")
        raw_metrics = value.get("test_metrics")
        if isinstance(raw_tests, Mapping):
            candidate = raw_tests
        elif isinstance(raw_metrics, Mapping):
            candidate = raw_metrics
        else:
            candidate = value
        if not isinstance(candidate, Mapping):
            return None
        values = {name: _number(candidate.get(name)) for name in _COUNT_NAMES}
        if values["total"] is None:
            return None
        for name in _COUNT_NAMES[1:]:
            if values[name] is None:
                values[name] = 0
        assert values["total"] is not None
        accounted = sum(values[name] for name in _COUNT_NAMES[2:])
        if values["passed"] == 0 and values["total"] >= accounted:
            values["passed"] = values["total"] - accounted
        if values["passed"] + accounted > values["total"]:
            return None
        failed = candidate.get("failed_tests", value.get("failed_tests", []))
        failed_tests = (
            [str(item) for item in failed]
            if isinstance(failed, list) and all(isinstance(item, (str, int)) for item in failed)
            else []
        )
        return {name: int(values[name]) for name in _COUNT_NAMES}, failed_tests

    def _from_structured(
        self,
        value: Mapping[str, Any],
        *,
        command: str,
        exit_code: int | None,
        duration_ms: int | None,
        source_locator: str,
        raw_log_locator: str,
    ) -> ParsedTestResult | None:
        parsed = self._counts(value)
        if parsed is None:
            return None
        tests, failed_tests = parsed
        coverage = value.get("coverage_metrics", {})
        if not isinstance(coverage, Mapping):
            coverage = {}
        return ParsedTestResult(
            parse_status="PARSED",
            parser_version=self.parser_version,
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            tests=tests,
            failed_tests=tuple(failed_tests),
            coverage_metrics=dict(coverage),
            source_locator=source_locator,
            raw_log_locator=raw_log_locator,
            confidence="structured",
        )

    def _from_junit(
        self,
        xml_text: str,
        *,
        command: str,
        exit_code: int | None,
        duration_ms: int | None,
        source_locator: str,
        raw_log_locator: str,
    ) -> ParsedTestResult | None:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        cases = list(root.iter("testcase"))
        if not cases:
            return None
        failed_tests: list[str] = []
        failed = skipped = errors = 0
        duration_total = 0.0
        for case in cases:
            name = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}".strip(":")
            time_value = case.attrib.get("time")
            try:
                duration_total += float(time_value or 0)
            except ValueError:
                pass
            if case.find("failure") is not None:
                failed += 1
                failed_tests.append(name)
            elif case.find("error") is not None:
                errors += 1
                failed_tests.append(name)
            elif case.find("skipped") is not None:
                skipped += 1
        total = len(cases)
        return ParsedTestResult(
            parse_status="PARSED",
            parser_version=self.parser_version,
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms if duration_ms is not None else int(duration_total * 1000),
            tests={
                "total": total,
                "passed": total - failed - skipped - errors,
                "failed": failed,
                "skipped": skipped,
                "error": errors,
            },
            failed_tests=tuple(failed_tests),
            coverage_metrics={},
            source_locator=source_locator,
            raw_log_locator=raw_log_locator,
            confidence="machine_readable",
        )

    def _from_console(
        self,
        text: str,
        *,
        command: str,
        exit_code: int | None,
        duration_ms: int | None,
        source_locator: str,
        raw_log_locator: str,
    ) -> ParsedTestResult | None:
        matches = {
            "passed": re.search(r"(?<!\d)(\d+)\s+passed\b", text),
            "failed": re.search(r"(?<!\d)(\d+)\s+failed\b", text),
            "skipped": re.search(r"(?<!\d)(\d+)\s+skipped\b", text),
            "error": re.search(r"(?<!\d)(\d+)\s+errors?\b", text),
        }
        if not any(matches.values()):
            return None
        counts = {
            name: int(match.group(1)) if match is not None else 0
            for name, match in matches.items()
        }
        counts["total"] = sum(counts.values())
        return ParsedTestResult(
            parse_status="PARSED",
            parser_version=self.parser_version,
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            tests={
                "total": counts["total"],
                "passed": counts["passed"],
                "failed": counts["failed"],
                "skipped": counts["skipped"],
                "error": counts["error"],
            },
            failed_tests=(),
            coverage_metrics={},
            source_locator=source_locator,
            raw_log_locator=raw_log_locator,
            confidence="console_fallback",
        )

    def parse(
        self,
        source: Mapping[str, Any] | str | Path,
        *,
        command: str,
        exit_code: int | None,
        duration_ms: int | None = None,
        source_locator: str = "",
        raw_log_locator: str = "",
    ) -> ParsedTestResult:
        context_error = self._validate_context(
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            source_locator=source_locator,
            raw_log_locator=raw_log_locator,
        )
        if context_error is not None:
            return context_error
        value: Any = source
        source_read: SourceReadResult | None = None
        if isinstance(source, Path):
            try:
                if self.source_cache is not None:
                    relative = source.resolve().relative_to(
                        self.source_cache.workspace_root
                    ).as_posix()
                    source_read = self.source_cache.read(
                        relative,
                        require_trusted_hash=False,
                    )
                    source_locator = source_locator or source_read.canonical_locator
                    cached = self.source_cache.get_parsed(source_read)
                    if isinstance(cached, Mapping) and isinstance(cached.get("parsed"), Mapping):
                        parsed = dict(cached["parsed"])
                        if (
                            parsed.get("command") == command
                            and parsed.get("exit_code") == exit_code
                            and parsed.get("duration_ms") == duration_ms
                            and parsed.get("raw_log_locator") == raw_log_locator
                        ):
                            self.telemetry.record_cache_event(
                                "hit", parser_version=self.parser_version
                            )
                            parsed["failed_tests"] = tuple(parsed.get("failed_tests", ()))
                            return ParsedTestResult(**parsed)
                    else:
                        self.telemetry.record_cache_event(
                            "miss",
                            invalidation_reason="parsed_cache_miss",
                            parser_version=self.parser_version,
                        )
                    value = source_read.content.decode("utf-8")
                else:
                    value = source.read_text(encoding="utf-8")
                    source_locator = source_locator or source.as_posix()
            except (OSError, UnicodeError, ValueError, RuntimeValidationError):
                value = None
        self.telemetry.record_parser_run()
        if isinstance(value, Mapping):
            result = self._from_structured(
                value,
                command=command,
                exit_code=exit_code,
                duration_ms=duration_ms,
                source_locator=source_locator,
                raw_log_locator=raw_log_locator,
            )
            if result is not None:
                return self._finish(result, source_read)
        if isinstance(value, str):
            stripped = value.lstrip()
            if stripped.startswith("<"):
                result = self._from_junit(
                    value,
                    command=command,
                    exit_code=exit_code,
                    duration_ms=duration_ms,
                    source_locator=source_locator,
                    raw_log_locator=raw_log_locator,
                )
                if result is not None:
                    return self._finish(result, source_read)
            result = self._from_console(
                value,
                command=command,
                exit_code=exit_code,
                duration_ms=duration_ms,
                source_locator=source_locator,
                raw_log_locator=raw_log_locator,
            )
            if result is not None:
                return self._finish(result, source_read)
        return self._unknown(
            command=command,
            exit_code=exit_code,
            duration_ms=duration_ms,
            source_locator=source_locator,
            raw_log_locator=raw_log_locator,
            reason="PARSE_AMBIGUOUS",
        )

    def persist(
        self,
        store: SessionStore,
        *,
        session_id: str,
        result: ParsedTestResult,
        result_id: str | None = None,
    ) -> str:
        return DerivedRuntimeStore(store).write_test_result(
            session_id=session_id,
            parser_version=self.parser_version,
            result=result.to_dict(),
            result_id=result_id,
        )


__all__ = ["PARSER_VERSION", "ParsedTestResult", "TestOutputParser"]
