"""Stage 3 Evaluator Calibration Benchmark 测试。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluator_calibration import (  # noqa: E402
    calibration_metrics,
    load_calibration_cases,
    run_calibration,
)


CASE_ROOT = ROOT / "tests" / "evaluator_calibration" / "cases"


def test_calibration_cases_have_independent_expected_files() -> None:
    cases = load_calibration_cases(CASE_ROOT)
    assert len(cases) == 12
    assert all(case.observed is not case.expected for case in cases)
    assert all("expected_result" not in case.observed for case in cases)


def test_calibration_has_no_critical_false_pass_or_routing_miss() -> None:
    records, metrics = run_calibration(CASE_ROOT)
    assert all(all(record["match"].values()) for record in records)
    assert metrics["pass_false_positive_rate"] == 0
    assert metrics["fail_false_negative_rate"] == 0
    assert metrics["critical_bug_miss_rate"] == 0
    assert metrics["correct_routing_rate"] == 1
    assert metrics["severity_agreement_rate"] == 1


def test_metrics_are_reproducible() -> None:
    cases = load_calibration_cases(CASE_ROOT)
    assert calibration_metrics(cases) == calibration_metrics(cases)
