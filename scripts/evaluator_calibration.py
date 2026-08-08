"""Evaluator Calibration Case 的确定性分类与统计。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_state import parse_project_yaml


RESULTS = {"PASS", "FAIL", "BLOCKED"}
SEVERITY_RANK = {
    "observation": 0,
    "minor": 1,
    "major": 2,
    "critical": 3,
    "blocker": 4,
}


@dataclass(frozen=True)
class CalibrationCase:
    name: str
    observed: dict[str, Any]
    expected: dict[str, Any]


@dataclass(frozen=True)
class CalibrationPrediction:
    result: str
    issue_classes: tuple[str, ...]
    severity: str
    route_to: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "issue_classes": list(self.issue_classes),
            "severity": self.severity,
            "route_to": self.route_to,
        }


def load_calibration_cases(root: str | Path) -> list[CalibrationCase]:
    """按目录名稳定加载 case 与 expected。"""

    base = Path(root).resolve()
    cases: list[CalibrationCase] = []
    case_paths = sorted(base.glob("case-*.yaml"))
    for case_path in case_paths:
        name = case_path.stem.removeprefix("case-")
        expected_path = base / f"expected-{name}.yaml"
        if not expected_path.is_file():
            raise ValueError(f"Calibration case 缺少配对 expected 文件：{name}")
        observed = parse_project_yaml(case_path.read_text(encoding="utf-8"))
        expected = parse_project_yaml(expected_path.read_text(encoding="utf-8"))
        if not isinstance(observed, dict) or not isinstance(expected, dict):
            raise ValueError(f"Calibration case 必须是对象：{name}")
        cases.append(CalibrationCase(name, observed, expected))
    return cases


def _open_issues(observed: dict[str, Any]) -> list[dict[str, Any]]:
    issues = observed.get("issues", [])
    if not isinstance(issues, list):
        return []
    return [
        item
        for item in issues
        if isinstance(item, dict) and item.get("status", "OPEN") in {"OPEN", "REOPENED"}
    ]


def _gate_failures(observed: dict[str, Any]) -> list[dict[str, Any]]:
    gates = observed.get("gates", [])
    if not isinstance(gates, list):
        return []
    return [
        item
        for item in gates
        if isinstance(item, dict)
        and item.get("required") is True
        and item.get("result") != "PASS"
    ]


def predict_case(observed: dict[str, Any]) -> CalibrationPrediction:
    """只根据 observed evidence 预测结果，不读取 expected。"""

    issues = _open_issues(observed)
    failures = _gate_failures(observed)
    environment_blocked = observed.get("environment_blocked") is True
    issue_classes = {str(item.get("category")) for item in issues if item.get("category")}
    severities = [
        str(item.get("severity"))
        for item in issues
        if item.get("severity") in SEVERITY_RANK
    ]
    route_candidates = [
        str(item.get("route_to"))
        for item in issues
        if item.get("route_to") in {"SYSTEM_OR_USER", "USER", "PLANNER", "GENERATOR"}
    ]
    browser = observed.get("browser_acceptance")
    if isinstance(browser, dict) and browser.get("result") != "PASS":
        failure_class = browser.get("failure_class")
        if failure_class == "evaluation_environment_blocked":
            environment_blocked = True
        else:
            issue_classes.add("implementation_defect")
            severities.append("critical")
    feature = observed.get("feature_completeness")
    if isinstance(feature, dict) and feature.get("result") not in {None, "PASS", "SKIPPED"}:
        if feature.get("result") == "BLOCKED":
            environment_blocked = True
        else:
            issue_classes.add("incomplete_implementation")
            severities.append("critical")
    for gate in failures:
        gate_id = gate.get("gate_id")
        if gate_id == "GATE-REGRESSION":
            issue_classes.add("regression")
            severities.append("critical")
        elif gate_id == "GATE-REQUIREMENTS":
            issue_classes.add("requirement_ambiguity")
            severities.append("major")
        elif gate_id == "GATE-BROWSER-ACCEPTANCE":
            issue_classes.add("implementation_defect")
            severities.append("critical")
        elif gate_id == "GATE-FEATURE-COMPLETENESS":
            issue_classes.add("incomplete_implementation")
            severities.append("critical")
    if observed.get("console_critical_error") is True:
        issue_classes.add("implementation_defect")
        severities.append("critical")
    if observed.get("generator_claimed_fixed") is True and failures:
        issue_classes.add("implementation_defect")
        severities.append("critical")
    if environment_blocked:
        return CalibrationPrediction(
            "BLOCKED",
            tuple(sorted(issue_classes)),
            "critical" if severities else "major",
            "SYSTEM_OR_USER",
        )
    if issues or failures or issue_classes:
        severity = max(severities or ["major"], key=lambda item: SEVERITY_RANK[item])
        route = (
            "PLANNER"
            if any(item in {"plan_gap", "scope_mismatch", "requirement_ambiguity"} for item in issue_classes)
            else max(route_candidates, key=lambda item: {"SYSTEM_OR_USER": 4, "USER": 3, "PLANNER": 2, "GENERATOR": 1}[item], default="GENERATOR")
        )
        return CalibrationPrediction("FAIL", tuple(sorted(issue_classes)), severity, route)
    return CalibrationPrediction("PASS", (), "observation", "ACCEPTED")


def _expected_result(case: CalibrationCase) -> str:
    result = case.expected.get("expected_result")
    if result not in RESULTS:
        raise ValueError(f"expected_result 无效：{case.name}")
    return str(result)


def calibration_metrics(cases: list[CalibrationCase]) -> dict[str, float | int]:
    """比较预测与人工 expected，返回稳定比例。"""

    if not cases:
        raise ValueError("Calibration suite 不能为空")
    predictions = [(case, predict_case(dict(case.observed))) for case in cases]
    expected_pass = [item for item in cases if _expected_result(item) == "PASS"]
    expected_fail = [item for item in cases if _expected_result(item) == "FAIL"]
    expected_critical = [
        item
        for item in cases
        if item.expected.get("minimum_severity") in {"critical", "blocker"}
    ]
    pass_false_positive = sum(
        1
        for case, prediction in predictions
        if _expected_result(case) != "PASS" and prediction.result == "PASS"
    )
    fail_false_negative = sum(
        1
        for case, prediction in predictions
        if _expected_result(case) == "PASS" and prediction.result != "PASS"
    )
    critical_miss = sum(
        1
        for case, prediction in predictions
        if case.expected.get("minimum_severity") in {"critical", "blocker"}
        and prediction.result == "PASS"
    )
    routing_matches = sum(
        1
        for case, prediction in predictions
        if prediction.route_to == case.expected.get("route_to")
    )
    severity_matches = sum(
        1
        for case, prediction in predictions
        if prediction.severity == case.expected.get("minimum_severity")
    )
    return {
        "total_cases": len(cases),
        "pass_false_positive_rate": pass_false_positive / max(1, len(expected_fail)),
        "fail_false_negative_rate": fail_false_negative / max(1, len(expected_pass)),
        "critical_bug_miss_rate": critical_miss / max(1, len(expected_critical)),
        "correct_routing_rate": routing_matches / len(cases),
        "severity_agreement_rate": severity_matches / len(cases),
    }


def run_calibration(root: str | Path) -> tuple[list[dict[str, Any]], dict[str, float | int]]:
    """运行全部用例并返回逐案报告和统计。"""

    cases = load_calibration_cases(root)
    records: list[dict[str, Any]] = []
    for case in cases:
        prediction = predict_case(dict(case.observed))
        records.append(
            {
                "case": case.name,
                "prediction": prediction.to_dict(),
                "expected": case.expected,
                "match": {
                    "result": prediction.result == case.expected.get("expected_result"),
                    "issue_classes": set(prediction.issue_classes)
                    == set(case.expected.get("expected_issue_classes", [])),
                    "route_to": prediction.route_to == case.expected.get("route_to"),
                    "severity": prediction.severity == case.expected.get("minimum_severity"),
                },
            }
        )
    return records, calibration_metrics(cases)
