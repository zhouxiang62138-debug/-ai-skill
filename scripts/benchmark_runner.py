"""受控 Harness Benchmark Runner；不触碰正式项目批准与安全规则。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .project_state import ProjectStateError, parse_project_yaml, serialize_project_state
except ImportError:  # 兼容 tests 直接把 scripts 加入 sys.path
    from project_state import ProjectStateError, parse_project_yaml, serialize_project_state

from runtime.deterministic.benchmark import (
    BENCHMARK_KINDS,
    BENCHMARK_SCHEMA_VERSION,
    BaselineBenchmarkHarness,
    BaselineSnapshot,
    BenchmarkCase,
    BenchmarkInputs,
    BenchmarkObservation,
    BenchmarkExecutionContext as F14GBenchmarkExecutionContext,
    default_benchmark_cases,
    tree_hash,
)


VARIANTS = (
    "generator_only",
    "planner_generator",
    "planner_generator_evaluator",
    "full_browser_completeness",
    "full_contract_rollover",
)
RESULT_PATTERN = re.compile(r"^benchmark-(\d{3})\.yaml$")
SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|secret|authorization)\s*[:=]"
)


@dataclass(frozen=True)
class BenchmarkResult:
    benchmark_id: str
    task_id: str
    variant: str
    model_id: str
    prompt_version: str
    policy_version: str
    rubric_version: str
    wall_clock_seconds: float
    token_usage: int
    estimated_cost: float
    tool_call_count: int
    compaction_count: int
    evaluator_rounds: int
    ac_pass_rate: float
    critical_bug_miss_rate: float
    false_pass_rate: float
    browser_coverage: float
    feature_completeness: float
    human_review_score: float | None
    selected_best_candidate: str | None
    best_candidate_is_final_round: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "benchmark_id": self.benchmark_id,
            "task_id": self.task_id,
            "variant": self.variant,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "policy_version": self.policy_version,
            "rubric_version": self.rubric_version,
            "wall_clock_seconds": self.wall_clock_seconds,
            "token_usage": self.token_usage,
            "estimated_cost": self.estimated_cost,
            "tool_call_count": self.tool_call_count,
            "compaction_count": self.compaction_count,
            "evaluator_rounds": self.evaluator_rounds,
            "ac_pass_rate": self.ac_pass_rate,
            "critical_bug_miss_rate": self.critical_bug_miss_rate,
            "false_pass_rate": self.false_pass_rate,
            "browser_coverage": self.browser_coverage,
            "feature_completeness": self.feature_completeness,
            "human_review_score": self.human_review_score,
            "selected_best_candidate": self.selected_best_candidate,
            "best_candidate_is_final_round": self.best_candidate_is_final_round,
        }


@dataclass(frozen=True)
class BenchmarkExecutionContext:
    """Benchmark 可用的最小 Capability Context。"""

    executor: Callable[[str], Mapping[str, Any]]
    capabilities: frozenset[str] = frozenset({"benchmark.execute"})

    def execute(self, variant: str) -> Mapping[str, Any]:
        return self.executor(variant)


def _validate_number(value: Any, label: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or value < minimum:
        raise ProjectStateError(f"Benchmark {label} 无效")
    if maximum is not None and value > maximum:
        raise ProjectStateError(f"Benchmark {label} 超出范围")
    return float(value)


def _validate_integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProjectStateError(f"Benchmark {label} 无效")
    return value


def _yaml_safe(value: Any) -> Any:
    """适配项目受限 YAML：浮点落盘为稳定十进制字符串。"""

    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, dict):
        return {key: _yaml_safe(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_yaml_safe(child) for child in value]
    return value


def _assert_test_project(project_root: Path, skill_root: Path | None) -> None:
    if not project_root.name.startswith("test_"):
        raise ProjectStateError("Benchmark 只能运行 test_ 前缀的受控测试项目")
    if skill_root is not None:
        try:
            project_root.relative_to(skill_root.resolve())
        except ValueError:
            return
        raise ProjectStateError("Benchmark 测试项目不得位于 Skill 本体仓库内")


class BenchmarkRunner:
    """把实验结果追加写入隔离输出目录，不改变正式项目状态。"""

    def __init__(self, output_root: str | Path, *, skill_root: str | Path | None = None) -> None:
        self.output_root = Path(output_root).resolve()
        self.skill_root = Path(skill_root).resolve() if skill_root else None

    def _next_id(self) -> tuple[str, Path]:
        self.output_root.mkdir(parents=True, exist_ok=True)
        existing = sorted(
            path for path in self.output_root.iterdir()
            if path.is_file() and RESULT_PATTERN.fullmatch(path.name)
        )
        number = len(existing) + 1
        return f"benchmark-{number:03d}", self.output_root / f"benchmark-{number:03d}.yaml"

    def run(
        self,
        *,
        project_root: str | Path,
        task_id: str,
        variant: str,
        model_id: str,
        prompt_version: str,
        policy_version: str,
        rubric_version: str,
        execute: Callable[[str], Mapping[str, Any]] | None = None,
        execution_context: Any | None = None,
        test_only: bool = False,
    ) -> BenchmarkResult:
        """运行一次受控变体；execute 只能返回实验指标，不可提交正式状态。"""

        root = Path(project_root).resolve()
        _assert_test_project(root, self.skill_root)
        if self.skill_root is not None:
            try:
                self.output_root.relative_to(self.skill_root)
            except ValueError:
                pass
            else:
                raise ProjectStateError("Benchmark output_root 不得位于 Skill 本体仓库内")
        if variant not in VARIANTS:
            raise ProjectStateError(f"未知 Benchmark variant：{variant}")
        for value, label in (
            (task_id, "task_id"),
            (model_id, "model_id"),
            (prompt_version, "prompt_version"),
            (policy_version, "policy_version"),
            (rubric_version, "rubric_version"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ProjectStateError(f"Benchmark {label} 必须是非空字符串")
        started = time.perf_counter()
        if execution_context is not None:
            capabilities = getattr(execution_context, "capabilities", ())
            if "benchmark.execute" not in set(capabilities) or not hasattr(execution_context, "execute"):
                raise ProjectStateError("Benchmark Execution/Capability Context 不允许 benchmark.execute")
            raw = execution_context.execute(variant)
        elif test_only and callable(execute):
            raw = execute(variant)
        else:
            raise ProjectStateError("正式 Benchmark 必须通过受限 Execution/Capability Context")
        elapsed = time.perf_counter() - started
        if not isinstance(raw, Mapping):
            raise ProjectStateError("Benchmark execute 必须返回指标对象")
        if SECRET_PATTERN.search(str(raw)):
            raise ProjectStateError("Benchmark 结果不得包含疑似 Secret")
        benchmark_id, target = self._next_id()
        result = BenchmarkResult(
            benchmark_id=benchmark_id,
            task_id=task_id,
            variant=variant,
            model_id=model_id,
            prompt_version=prompt_version,
            policy_version=policy_version,
            rubric_version=rubric_version,
            wall_clock_seconds=_validate_number(raw.get("wall_clock_seconds", elapsed), "wall_clock_seconds"),
            token_usage=_validate_integer(raw.get("token_usage", 0), "token_usage"),
            estimated_cost=_validate_number(raw.get("estimated_cost", 0.0), "estimated_cost"),
            tool_call_count=_validate_integer(raw.get("tool_call_count", 0), "tool_call_count"),
            compaction_count=_validate_integer(raw.get("compaction_count", 0), "compaction_count"),
            evaluator_rounds=_validate_integer(raw.get("evaluator_rounds", 0), "evaluator_rounds"),
            ac_pass_rate=_validate_number(raw.get("ac_pass_rate", 0.0), "ac_pass_rate", maximum=1.0),
            critical_bug_miss_rate=_validate_number(raw.get("critical_bug_miss_rate", 0.0), "critical_bug_miss_rate", maximum=1.0),
            false_pass_rate=_validate_number(raw.get("false_pass_rate", 0.0), "false_pass_rate", maximum=1.0),
            browser_coverage=_validate_number(raw.get("browser_coverage", 0.0), "browser_coverage", maximum=1.0),
            feature_completeness=_validate_number(raw.get("feature_completeness", 0.0), "feature_completeness", maximum=10.0),
            human_review_score=(
                None if raw.get("human_review_score") is None
                else _validate_number(raw["human_review_score"], "human_review_score", maximum=10.0)
            ),
            selected_best_candidate=(
                None if raw.get("selected_best_candidate") is None
                else str(raw["selected_best_candidate"])
            ),
            best_candidate_is_final_round=raw.get("best_candidate_is_final_round", False),
        )
        if not isinstance(result.best_candidate_is_final_round, bool):
            raise ProjectStateError("Benchmark best_candidate_is_final_round 必须是 bool")
        payload = serialize_project_state(_yaml_safe(result.to_dict())).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        document = _yaml_safe(result.to_dict())
        document["result_hash"] = digest
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except FileExistsError as exc:
            raise ProjectStateError("Benchmark 结果拒绝覆盖历史记录") from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialize_project_state(document).encode("utf-8"))
        return result


__all__ = [
    "BENCHMARK_KINDS",
    "BENCHMARK_SCHEMA_VERSION",
    "BaselineBenchmarkHarness",
    "BaselineSnapshot",
    "BenchmarkCase",
    "BenchmarkExecutionContext",
    "F14GBenchmarkExecutionContext",
    "BenchmarkInputs",
    "BenchmarkObservation",
    "BenchmarkResult",
    "BenchmarkRunner",
    "VARIANTS",
    "default_benchmark_cases",
    "tree_hash",
]
