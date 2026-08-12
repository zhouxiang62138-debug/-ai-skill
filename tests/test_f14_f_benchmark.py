"""F14-F A/B 与 Evaluator Detection Parity 测试。"""

from runtime.deterministic.benchmark import BaselineSnapshot
from runtime.deterministic.f14_benchmark import (
    F14BenchmarkInputs,
    F14BenchmarkObservation,
    F14ControlledABBenchmark,
    F14EfficiencyMetrics,
    F14QualityMetrics,
)
from runtime.f14_control import F14BaselineFreeze


def _baseline() -> BaselineSnapshot:
    return BaselineSnapshot(
        baseline_id="F14-G-BASELINE-001",
        code_commit_or_tree_hash="tree",
        runtime_schema=13,
        config_hash="config",
        benchmark_fixture_hash="fixture",
        model_id="f14-g-controlled-fake-model",
        environment_hash="environment",
        evaluation_profile_hash="profile",
        inputs_fingerprint="inputs",
    )


def _inputs() -> F14BenchmarkInputs:
    return F14BenchmarkInputs(
        case_id="A",
        project_id="test_case_a",
        request_fingerprint="request",
        approved_requirements_hash="requirements",
        approved_plan_hash="plan",
        project_revision=1,
        model_fixture_hash="fixture",
        evaluation_profile_hash="profile",
        environment_policy_hash="environment",
        config_hash="config",
    )


def test_ab_benchmark_compares_net_context_and_detection_parity() -> None:
    baseline = _baseline()
    inputs = _inputs()
    baseline_observation = F14BenchmarkObservation(
        baseline=baseline,
        inputs=inputs,
        mode="F13_FULL",
        efficiency=F14EfficiencyMetrics(
            formal_context_bytes=1000,
            initial_selective_bytes=1000,
            repeated_context_bytes=500,
            real_model_requests=4,
            lifecycle_invocations=4,
        ),
        quality=F14QualityMetrics(),
    )
    selective_observation = F14BenchmarkObservation(
        baseline=baseline,
        inputs=inputs,
        mode="F14_SELECTIVE",
        efficiency=F14EfficiencyMetrics(
            formal_context_bytes=1000,
            initial_selective_bytes=400,
            expansion_bytes=100,
            repeated_context_bytes=100,
            real_model_requests=4,
            python_only_executions=2,
            lifecycle_invocations=6,
        ),
        quality=F14QualityMetrics(),
    )
    freeze = F14BaselineFreeze(tuple(
        BaselineSnapshot(
            baseline_id=f"F14-G-BASELINE-{index:03d}",
            code_commit_or_tree_hash="tree",
            runtime_schema=13,
            config_hash="config",
            benchmark_fixture_hash="fixture",
            model_id="f14-g-controlled-fake-model",
            environment_hash="environment",
            evaluation_profile_hash="profile",
            inputs_fingerprint="inputs",
        )
        for index in range(1, 6)
    ))
    comparison = F14ControlledABBenchmark(freeze).compare(
        baseline_observation, selective_observation
    )
    assert comparison.result == "PASS"
    assert comparison.net_context_reduction == 500
    assert comparison.repeated_context_reduction == 400
    assert comparison.detection_parity["detected_issues"] == []


def test_ab_benchmark_fails_on_detection_difference_even_if_quality_passes() -> None:
    baseline = _baseline()
    inputs = _inputs()
    common = dict(
        baseline=baseline,
        inputs=inputs,
        efficiency=F14EfficiencyMetrics(1000, 500),
        quality=F14QualityMetrics(),
    )
    baseline_observation = F14BenchmarkObservation(mode="F13_FULL", **common, detected_issue_ids=("ISSUE-A",))
    selective_observation = F14BenchmarkObservation(mode="F14_SELECTIVE", **common)
    freeze = F14BaselineFreeze(tuple(
        BaselineSnapshot(
            baseline_id=f"F14-G-BASELINE-{index:03d}",
            code_commit_or_tree_hash="tree", runtime_schema=13, config_hash="config",
            benchmark_fixture_hash="fixture", model_id="model", environment_hash="environment",
            evaluation_profile_hash="profile", inputs_fingerprint="inputs",
        ) for index in range(1, 6)
    ))
    comparison = F14ControlledABBenchmark(freeze).compare(baseline_observation, selective_observation)
    assert comparison.result == "FAIL"
    assert comparison.detection_parity["detected_issues"] == ["ISSUE-A"]
