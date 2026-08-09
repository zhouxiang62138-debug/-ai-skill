"""Adaptive Harness Policy 与 blind calibration 的确定性测试。"""

from __future__ import annotations

import pytest

from runtime.harness_policy import HarnessPolicy
from runtime.contract_preflight import run_contract_preflight
from scripts.evaluator_calibration import (
    BlindCalibrationCase,
    blind_calibration_metrics,
    predict_blind_case,
    validate_blind_observation,
)


def test_unknown_model_and_browser_requirement_fail_closed_to_full() -> None:
    policy = HarnessPolicy()
    unknown = policy.choose(model_id="unregistered-model", task_complexity="low")
    assert unknown.mode == "FULL"
    assert "unknown_model_or_capability" in unknown.reason_codes
    browser = policy.choose(
        model_id="gpt-5",
        model_capability_profile="general_reasoning",
        task_complexity="low",
        browser_required=True,
    )
    assert browser.mode == "FULL"
    assert "browser_acceptance" in browser.required_gates


def test_low_risk_known_model_can_use_lean_but_keeps_mandatory_gates() -> None:
    decision = HarnessPolicy().choose(
        model_id="gpt-5",
        model_capability_profile="general_reasoning",
        task_complexity="low",
        acceptance_criteria_count=1,
        key_workflow_count=1,
    )
    assert decision.mode == "LEAN"
    assert {"source_chain", "evidence", "regression"} <= set(decision.required_gates)


def test_blind_calibration_does_not_accept_preclassified_input() -> None:
    with pytest.raises(ValueError, match="预先分类字段"):
        validate_blind_observation({"browser_trace": [], "severity": "critical"})
    case = BlindCalibrationCase(
        "one",
        {"issues": [{"status": "OPEN", "category": "implementation_defect"}]},
    )
    prediction = predict_blind_case(case)
    metrics = blind_calibration_metrics(
        {"one": prediction},
        {
            "one": {
                "result": "FAIL",
                "issue_classes": ["implementation_defect"],
                "severity": "major",
                "route_to": "GENERATOR",
            }
        },
    )
    assert metrics["total_cases"] == 1
    assert metrics["result_accuracy"] == 1


def test_blind_calibration_rejects_nested_preclassified_fields() -> None:
    with pytest.raises(ValueError, match="category"):
        validate_blind_observation({"browser_trace": [{"category": "bug"}]})
    with pytest.raises(ValueError, match="result"):
        validate_blind_observation({"browser_trace": [{"result": "PASS"}]})


def test_contract_preflight_feature_none_fails_closed() -> None:
    result = run_contract_preflight(
        ".",
        feature=None,  # type: ignore[arg-type]
        approved_plan="memory/plans/plan-001.md",
        approved_requirements=set(),
        approved_acceptance_criteria=set(),
    )
    assert result.passed is False
    assert result.required is True
