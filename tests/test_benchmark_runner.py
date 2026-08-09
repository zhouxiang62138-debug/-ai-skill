"""Benchmark Runner 的隔离、追加和指标记录测试。"""

from __future__ import annotations

import pytest

from scripts.benchmark_runner import BenchmarkRunner
from scripts.project_state import ProjectStateError


def test_benchmark_runner_requires_external_test_project_and_appends_result(tmp_path) -> None:
    skill_root = tmp_path / "skill"
    project_root = tmp_path / "test_controlled_app"
    output_root = tmp_path / "benchmark-results"
    project_root.mkdir()
    runner = BenchmarkRunner(output_root, skill_root=skill_root)
    result = runner.run(
        project_root=project_root,
        task_id="todo-core",
        variant="full_contract_rollover",
        model_id="gpt-5",
        prompt_version="generator-v2",
        policy_version="harness-v1",
        rubric_version="rubric-v1",
        execute=lambda variant: {
            "token_usage": 100,
            "estimated_cost": 0.01,
            "tool_call_count": 4,
            "compaction_count": 1,
            "evaluator_rounds": 1,
            "ac_pass_rate": 1.0,
            "critical_bug_miss_rate": 0.0,
            "false_pass_rate": 0.0,
            "browser_coverage": 1.0,
            "feature_completeness": 9.0,
            "selected_best_candidate": "candidate-001",
            "best_candidate_is_final_round": True,
        },
        test_only=True,
    )
    assert result.benchmark_id == "benchmark-001"
    assert (output_root / "benchmark-001.yaml").is_file()


def test_benchmark_runner_rejects_skill_directory_and_secret(tmp_path) -> None:
    project_root = tmp_path / "not_test_project"
    project_root.mkdir()
    runner = BenchmarkRunner(tmp_path / "out", skill_root=tmp_path)
    with pytest.raises(ProjectStateError, match="test_"):
        runner.run(
            project_root=project_root,
            task_id="x",
            variant="generator_only",
            model_id="gpt-5",
            prompt_version="p",
            policy_version="h",
            rubric_version="r",
            execute=lambda _: {},
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("ac_pass_rate", 1.1),
        ("critical_bug_miss_rate", float("nan")),
        ("false_pass_rate", float("inf")),
        ("feature_completeness", -0.1),
        ("best_candidate_is_final_round", 1),
    ],
)
def test_benchmark_runner_rejects_invalid_numbers_and_bool_coercion(tmp_path, field, value) -> None:
    project_root = tmp_path / "test_controlled_app"
    project_root.mkdir()
    runner = BenchmarkRunner(tmp_path / "out", skill_root=tmp_path / "skill")

    def execute(_: str):
        return {field: value}

    with pytest.raises(ProjectStateError):
        runner.run(
            project_root=project_root,
            task_id="x",
            variant="generator_only",
            model_id="gpt-5",
            prompt_version="p",
            policy_version="h",
            rubric_version="r",
            execute=execute,
        )
