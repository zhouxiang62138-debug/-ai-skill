"""F14-G 基线 Benchmark Harness。

本模块只负责观察、固定输入和写入不可覆盖的 Benchmark 证据。它不会调用
Invocation Gate，也不会把 Shadow Candidate Context 交给正式模型适配器。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from ..session_store import RUNTIME_SCHEMA_VERSION
from .telemetry import RuntimeTelemetry


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_KINDS = frozenset({"synthetic", "controlled", "real_model"})
BASELINE_PATTERN = re.compile(r"^F14-G-BASELINE-(\d{3})$")
SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|password|secret|authorization)\s*[:=]"
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{label} must be a non-empty bounded string")
    if SECRET_PATTERN.search(value):
        raise ValueError(f"{label} contains a forbidden secret-like field")
    return value


@dataclass(frozen=True)
class BenchmarkCase:
    """一个固定的产品复杂度/设计深度/风险维度场景。"""

    case_id: str
    title: str
    initial_request: str
    route: tuple[str, ...]
    dimensions: Mapping[str, str]
    browser_scenarios: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.case_id not in {"A", "B", "C", "D", "E"}:
            raise ValueError("F14-G benchmark case must be A, B, C, D or E")
        if not self.route or any(not isinstance(item, str) or not item for item in self.route):
            raise ValueError("benchmark route is required")
        if SECRET_PATTERN.search(self.initial_request):
            raise ValueError("benchmark request contains a forbidden secret-like field")

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "title": self.title,
            "initial_request": self.initial_request,
            "route": list(self.route),
            "dimensions": dict(sorted(self.dimensions.items())),
            "browser_scenarios": list(self.browser_scenarios),
        }


def default_benchmark_cases() -> dict[str, BenchmarkCase]:
    """返回 F14-G 要求的 A-E 五类固定案例。"""

    common = ("requirements_discovery", "planning", "implementation", "evaluation")
    return {
        "A": BenchmarkCase(
            "A", "Simple / Simple", "创建一个普通倒计时工具。", common,
            {"product_complexity": "low", "design_ambition": "low", "risk_level": "low"},
            ("create_timer", "start_and_reset"),
        ),
        "B": BenchmarkCase(
            "B", "Simple Product + Showcase Design", "创建一个具有 premium 展示视觉的倒计时 App。",
            common + ("design_exploration",),
            {"product_complexity": "low", "design_ambition": "high", "interaction_ambition": "high", "risk_level": "low"},
            ("create_timer", "animate_progress", "responsive_showcase"),
        ),
        "C": BenchmarkCase(
            "C", "Standard Product", "创建一个支持收支、分类、统计和本地数据的个人记账 App。",
            common + ("design_exploration", "rework"),
            {"product_complexity": "standard", "technical_complexity": "standard", "data_complexity": "standard", "risk_level": "medium"},
            ("add_income", "add_expense", "filter_report"),
        ),
        "D": BenchmarkCase(
            "D", "Complex Business", "创建一个带多角色、权限、导入、报表和外部集成的销售分析系统。",
            common + ("change_request", "rework"),
            {"product_complexity": "high", "technical_complexity": "high", "data_complexity": "high", "integration_complexity": "high", "risk_level": "high"},
            ("import_sales", "role_restricted_report", "integration_failure"),
        ),
        "E": BenchmarkCase(
            "E", "Low Complexity / High Risk", "创建一个用于 UI 和数据工具模拟的医疗剂量计算器。",
            common + ("research", "rework"),
            {"product_complexity": "low", "technical_complexity": "low", "risk_level": "critical", "quality_rigor": "critical"},
            ("valid_dose", "invalid_input", "unit_conversion"),
        ),
    }


@dataclass(frozen=True)
class BenchmarkInputs:
    """A/B 比较必须保持一致的全部输入。"""

    project_id: str
    initial_user_request: str
    approved_requirements_hash: str
    approved_plan_hash: str
    project_revision: int
    model_id: str
    evaluation_profile_hash: str
    test_commands: tuple[str, ...]
    browser_scenarios: tuple[str, ...]
    environment_policy_hash: str
    config_hash: str
    project_tree_hash: str = ""

    def __post_init__(self) -> None:
        for name in (
            "project_id", "initial_user_request", "approved_requirements_hash",
            "approved_plan_hash", "model_id", "evaluation_profile_hash",
            "environment_policy_hash", "config_hash",
        ):
            _safe_text(getattr(self, name), name)
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise ValueError("project_revision must be non-negative")
        for name in ("test_commands", "browser_scenarios"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, str) or not item for item in values):
                raise ValueError(f"{name} must be a tuple of strings")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "initial_user_request": self.initial_user_request,
            "approved_requirements_hash": self.approved_requirements_hash,
            "approved_plan_hash": self.approved_plan_hash,
            "project_revision": self.project_revision,
            "model_id": self.model_id,
            "evaluation_profile_hash": self.evaluation_profile_hash,
            "test_commands": list(self.test_commands),
            "browser_scenarios": list(self.browser_scenarios),
            "environment_policy_hash": self.environment_policy_hash,
            "config_hash": self.config_hash,
            "project_tree_hash": self.project_tree_hash,
        }

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())


@dataclass(frozen=True)
class BaselineSnapshot:
    baseline_id: str
    code_commit_or_tree_hash: str
    runtime_schema: int
    config_hash: str
    benchmark_fixture_hash: str
    model_id: str
    environment_hash: str
    evaluation_profile_hash: str
    inputs_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "code_commit_or_tree_hash": self.code_commit_or_tree_hash,
            "runtime_schema": self.runtime_schema,
            "config_hash": self.config_hash,
            "benchmark_fixture_hash": self.benchmark_fixture_hash,
            "model_id": self.model_id,
            "environment_hash": self.environment_hash,
            "evaluation_profile_hash": self.evaluation_profile_hash,
            "inputs_fingerprint": self.inputs_fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BaselineSnapshot":
        return cls(
            baseline_id=str(value["baseline_id"]),
            code_commit_or_tree_hash=str(value["code_commit_or_tree_hash"]),
            runtime_schema=int(value["runtime_schema"]),
            config_hash=str(value["config_hash"]),
            benchmark_fixture_hash=str(value["benchmark_fixture_hash"]),
            model_id=str(value["model_id"]),
            environment_hash=str(value["environment_hash"]),
            evaluation_profile_hash=str(value["evaluation_profile_hash"]),
            inputs_fingerprint=str(value["inputs_fingerprint"]),
        )


@dataclass(frozen=True)
class BenchmarkExecutionContext:
    case: BenchmarkCase
    inputs: BenchmarkInputs
    telemetry: RuntimeTelemetry
    benchmark_kind: str


@dataclass(frozen=True)
class BenchmarkObservation:
    baseline: BaselineSnapshot
    case: BenchmarkCase
    inputs: BenchmarkInputs
    benchmark_kind: str
    telemetry: Mapping[str, Any]
    quality: Mapping[str, Any]
    quality_gates: Mapping[str, Any]
    wall_clock_ms: int
    result_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "benchmark_kind": self.benchmark_kind,
            "baseline": self.baseline.to_dict(),
            "case": self.case.to_dict(),
            "inputs": self.inputs.to_dict(),
            "telemetry": dict(self.telemetry),
            "quality": dict(self.quality),
            "quality_gates": dict(self.quality_gates),
            "wall_clock_ms": self.wall_clock_ms,
            "result_hash": self.result_hash,
        }


def tree_hash(root: str | Path, *, exclude: tuple[str, ...] = (".git", ".runtime", "__pycache__")) -> str:
    """对项目树做稳定哈希；不把绝对路径、原文日志或 Secret 写入结果。"""

    base = Path(root).resolve()
    if not base.is_dir():
        raise ValueError("benchmark project root must be a directory")
    records: list[tuple[str, str, int]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or any(part in exclude for part in path.parts):
            continue
        relative = path.relative_to(base).as_posix()
        raw = path.read_bytes()
        records.append((relative, hashlib.sha256(raw).hexdigest(), len(raw)))
    return _hash(records)


def _next_baseline_id(output_root: Path) -> str:
    values: list[int] = []
    for path in output_root.glob("baseline-*.json"):
        match = re.match(r"baseline-(\d{3})\.json$", path.name)
        if match:
            values.append(int(match.group(1)))
    return f"F14-G-BASELINE-{(max(values) + 1 if values else 1):03d}"


def _quality_gates(quality: Mapping[str, Any]) -> dict[str, Any]:
    def number(name: str) -> int:
        value = quality.get(name, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    mandatory_total = number("mandatory_total")
    required_total = number("required_ac_total")
    gates = {
        "requirement_coverage": number("mandatory_missing") == 0 and number("mandatory_unknown") == 0 and number("mandatory_stale") == 0 and number("mandatory_conflict") == 0 and number("mandatory_covered") == mandatory_total,
        "approval_integrity": number("approval_chain_failures") == 0,
        "implementation_tests": number("regression_failures") == 0,
        "evaluator_findings": number("evaluator_independence_failures") == 0,
        "regression_detection": number("regression_failures") == 0,
        "browser_results": number("browser_gate_failures") == 0,
        "security_constraints": number("security_constraint_failures") == 0,
        "privacy_constraints": number("privacy_constraint_failures") == 0,
        "false_pass": number("false_pass") == 0,
        "false_block": number("false_block") == 0,
        "required_acceptance_criteria": number("required_ac_covered") == required_total,
        "critical_false_omission": number("critical_false_omission") == 0,
    }
    return {"passes": all(gates.values()), "gates": gates}


class BaselineBenchmarkHarness:
    """通过受控回调驱动真实 Runtime 路径并生成不可覆盖基线。"""

    def __init__(
        self,
        output_root: str | Path,
        *,
        skill_root: str | Path | None = None,
        runtime_schema: int = RUNTIME_SCHEMA_VERSION,
        cases: Mapping[str, BenchmarkCase] | None = None,
    ) -> None:
        self.output_root = Path(output_root).resolve()
        self.skill_root = Path(skill_root).resolve() if skill_root else None
        self.runtime_schema = runtime_schema
        self.cases = dict(cases or default_benchmark_cases())

    def _assert_external_test_project(self, project_root: Path) -> None:
        if not project_root.name.startswith("test_"):
            raise ValueError("F14-G benchmark project must use test_ prefix")
        if self.skill_root is not None:
            try:
                project_root.relative_to(self.skill_root)
            except ValueError:
                return
            raise ValueError("benchmark data must not be stored in the Skill repository")

    def _snapshot(self, project_root: Path, case: BenchmarkCase, inputs: BenchmarkInputs) -> BaselineSnapshot:
        self.output_root.mkdir(parents=True, exist_ok=True)
        project_hash = inputs.project_tree_hash or tree_hash(project_root)
        fixture_hash = _hash({"case": case.to_dict(), "inputs": inputs.to_dict()})
        for path in sorted(self.output_root.glob("baseline-*.json")):
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                snapshot = BaselineSnapshot.from_mapping(existing)
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if (
                snapshot.inputs_fingerprint == inputs.fingerprint
                and snapshot.benchmark_fixture_hash == fixture_hash
            ):
                return snapshot
        baseline = BaselineSnapshot(
            baseline_id=_next_baseline_id(self.output_root),
            code_commit_or_tree_hash=project_hash,
            runtime_schema=self.runtime_schema,
            config_hash=inputs.config_hash,
            benchmark_fixture_hash=fixture_hash,
            model_id=inputs.model_id,
            environment_hash=inputs.environment_policy_hash,
            evaluation_profile_hash=inputs.evaluation_profile_hash,
            inputs_fingerprint=inputs.fingerprint,
        )
        target = self.output_root / f"baseline-{baseline.baseline_id.rsplit('-', 1)[-1]}.json"
        payload = _canonical(baseline.to_dict()).encode("utf-8")
        if target.exists():
            if target.read_bytes() != payload:
                raise ValueError("baseline snapshot is immutable and conflicts with existing evidence")
        else:
            with target.open("xb") as handle:
                handle.write(payload)
        return baseline

    @staticmethod
    def _execute(
        execute: Callable[..., Mapping[str, Any]],
        context: BenchmarkExecutionContext,
    ) -> Mapping[str, Any]:
        signature = inspect.signature(execute)
        positional = [
            parameter for parameter in signature.parameters.values()
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) == 0:
            value = execute()
        elif len(positional) == 1:
            value = execute(context)
        else:
            value = execute(context.case, context.inputs, context.telemetry)
        if not isinstance(value, Mapping):
            raise ValueError("benchmark executor must return a mapping")
        return value

    def run_case(
        self,
        *,
        project_root: str | Path,
        case_id: str,
        inputs: BenchmarkInputs,
        execute: Callable[..., Mapping[str, Any]],
        benchmark_kind: str = "controlled",
    ) -> BenchmarkObservation:
        if benchmark_kind not in BENCHMARK_KINDS:
            raise ValueError("benchmark_kind must be synthetic, controlled or real_model")
        root = Path(project_root).resolve()
        self._assert_external_test_project(root)
        if case_id not in self.cases:
            raise ValueError(f"unknown F14-G case: {case_id}")
        case = self.cases[case_id]
        if tuple(inputs.browser_scenarios) != tuple(case.browser_scenarios):
            raise ValueError("benchmark browser scenarios must be fixed per case")
        baseline = self._snapshot(root, case, inputs)
        telemetry = RuntimeTelemetry()
        context = BenchmarkExecutionContext(case, inputs, telemetry, benchmark_kind)
        started = time.perf_counter()
        raw = self._execute(execute, context)
        wall_clock_ms = max(0, int((time.perf_counter() - started) * 1000))
        raw_telemetry = raw.get("telemetry")
        if isinstance(raw_telemetry, RuntimeTelemetry):
            telemetry = raw_telemetry
        elif isinstance(raw_telemetry, Mapping):
            telemetry = RuntimeTelemetry.from_mapping(raw_telemetry)
        quality = raw.get("quality", telemetry.quality)
        if not isinstance(quality, Mapping):
            raise ValueError("benchmark quality must be a mapping")
        quality = dict(quality)
        quality_gates = _quality_gates(quality)
        document_without_hash = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "benchmark_kind": benchmark_kind,
            "baseline": baseline.to_dict(),
            "case": case.to_dict(),
            "inputs": inputs.to_dict(),
            "telemetry": telemetry.to_dict(),
            "quality": quality,
            "quality_gates": quality_gates,
            "wall_clock_ms": wall_clock_ms,
        }
        result_hash = _hash(document_without_hash)
        observation = BenchmarkObservation(
            baseline=baseline,
            case=case,
            inputs=inputs,
            benchmark_kind=benchmark_kind,
            telemetry=telemetry.to_dict(),
            quality=quality,
            quality_gates=quality_gates,
            wall_clock_ms=wall_clock_ms,
            result_hash=result_hash,
        )
        target = self.output_root / f"case-{case_id}-{result_hash[:12]}.json"
        payload = _canonical(observation.to_dict()).encode("utf-8")
        if target.exists():
            if target.read_bytes() != payload:
                raise ValueError("benchmark result already exists with conflicting evidence")
            return observation
        with target.open("xb") as handle:
            handle.write(payload)
        return observation

    def write_report(self, observations: list[BenchmarkObservation], target: str | Path) -> Path:
        """写入安全摘要；报告不包含模型原文或原始工具日志。"""

        path = Path(target).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# F14-G Telemetry & Baseline Benchmark Report",
            "",
            "本报告只记录 Behavior-neutral 观测结果。正式 Context 仍为 F13 Full Context，Candidate 仅作 Shadow Evidence。",
            "",
            "| Case | Benchmark Kind | Baseline ID | Real LLM Requests | Context Reuse | Candidate Reduction | Quality Gates |",
            "| --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for observation in observations:
            telemetry = observation.telemetry
            model = telemetry.get("model_efficiency", {})
            context = telemetry.get("context_ratios", {})
            lines.append(
                f"| {observation.case.case_id} | {observation.benchmark_kind} | {observation.baseline.baseline_id} | "
                f"{model.get('actual_model_requests', 0)} | {float(context.get('context_reuse_ratio', 0.0)):.4f} | "
                f"{float(context.get('candidate_reduction_ratio', 0.0)):.4f} | "
                f"{'PASS' if observation.quality_gates.get('passes') else 'FAIL'} |"
            )
        content = "\n".join(lines) + "\n"
        if SECRET_PATTERN.search(content):
            raise ValueError("report contains a secret-like field")
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("benchmark report is append-only and cannot be overwritten")
        else:
            path.write_text(content, encoding="utf-8", newline="\n")
        return path


__all__ = [
    "BENCHMARK_KINDS",
    "BENCHMARK_SCHEMA_VERSION",
    "BaselineBenchmarkHarness",
    "BaselineSnapshot",
    "BenchmarkCase",
    "BenchmarkExecutionContext",
    "BenchmarkInputs",
    "BenchmarkObservation",
    "default_benchmark_cases",
    "tree_hash",
]
