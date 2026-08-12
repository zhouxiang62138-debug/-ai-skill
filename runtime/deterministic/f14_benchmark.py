"""F14-F 控制型 A/B Benchmark 与质量奇偶校验。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from runtime.errors import RuntimeValidationError

from .benchmark import BaselineSnapshot
from ..f14_control import F14BaselineFreeze


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class F14BenchmarkInputs:
    """A/B 两侧必须固定的输入指纹。"""

    case_id: str
    project_id: str
    request_fingerprint: str
    approved_requirements_hash: str
    approved_plan_hash: str
    project_revision: int
    model_fixture_hash: str
    evaluation_profile_hash: str
    environment_policy_hash: str
    config_hash: str

    def __post_init__(self) -> None:
        if self.case_id not in {"A", "B", "C", "D", "E"}:
            raise RuntimeValidationError("F14_BENCHMARK_CASE_INVALID")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("F14_BENCHMARK_REVISION_INVALID")
        for name in (
            "project_id", "request_fingerprint", "approved_requirements_hash",
            "approved_plan_hash", "model_fixture_hash", "evaluation_profile_hash",
            "environment_policy_hash", "config_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise RuntimeValidationError("F14_BENCHMARK_INPUT_INVALID")

    @property
    def fingerprint(self) -> str:
        return _hash(self.__dict__)


@dataclass(frozen=True)
class F14QualityMetrics:
    """质量指标优先于效率指标；全部以差值记录。"""

    mandatory_coverage: int = 0
    critical_false_omission: int = 0
    false_omission: int = 0
    false_mandatory: int = 0
    false_block: int = 0
    approval_integrity_failures: int = 0
    required_ac_failures: int = 0
    implementation_test_failures: int = 0
    regression_findings: int = 0
    browser_findings: int = 0
    security_misses: int = 0
    privacy_misses: int = 0
    evaluator_detection_difference: int = 0
    e1_regression: int = 0
    existing_regression: int = 0

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, int) or value < 0:
                raise RuntimeValidationError(f"F14_BENCHMARK_QUALITY_{name.upper()}_INVALID")

    @property
    def pass_gate(self) -> bool:
        return all(value == 0 for name, value in self.__dict__.items() if name != "mandatory_coverage")

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class F14EfficiencyMetrics:
    formal_context_bytes: int
    initial_selective_bytes: int
    expansion_bytes: int = 0
    recovery_additional_bytes: int = 0
    repeated_context_bytes: int = 0
    context_reuse_ratio: float = 0.0
    real_model_requests: int = 0
    python_only_executions: int = 0
    lifecycle_invocations: int = 0
    runtime_latency_ms: int = 0
    input_tokens: int | str = "unavailable"
    cached_input_tokens: int | str = "unavailable"
    output_tokens: int | str = "unavailable"

    def __post_init__(self) -> None:
        for name in (
            "formal_context_bytes", "initial_selective_bytes", "expansion_bytes",
            "recovery_additional_bytes", "repeated_context_bytes", "real_model_requests",
            "python_only_executions", "lifecycle_invocations", "runtime_latency_ms",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise RuntimeValidationError(f"F14_BENCHMARK_EFFICIENCY_{name.upper()}_INVALID")
        if not 0 <= self.context_reuse_ratio <= 1:
            raise RuntimeValidationError("F14_BENCHMARK_CONTEXT_REUSE_RATIO_INVALID")

    @property
    def net_context_bytes(self) -> int:
        return self.initial_selective_bytes + self.expansion_bytes + self.recovery_additional_bytes

    @property
    def net_reduction_ratio(self) -> float:
        return max(0, self.formal_context_bytes - self.net_context_bytes) / self.formal_context_bytes if self.formal_context_bytes else 0.0

    @property
    def repeated_context_reduction(self) -> int:
        return self.repeated_context_bytes

    @property
    def python_only_ratio(self) -> float:
        total = self.real_model_requests + self.python_only_executions
        return self.python_only_executions / total if total else 0.0

    @property
    def token_status(self) -> str:
        values = (self.input_tokens, self.cached_input_tokens, self.output_tokens)
        if all(isinstance(value, int) for value in values):
            return "actual"
        if any(value != "unavailable" for value in values):
            return "estimated"
        return "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.__dict__,
            "net_context_bytes": self.net_context_bytes,
            "net_reduction_ratio": self.net_reduction_ratio,
            "python_only_ratio": self.python_only_ratio,
            "token_status": self.token_status,
        }


@dataclass(frozen=True)
class F14BenchmarkObservation:
    baseline: BaselineSnapshot
    inputs: F14BenchmarkInputs
    mode: str
    efficiency: F14EfficiencyMetrics
    quality: F14QualityMetrics
    detected_issue_ids: tuple[str, ...] = ()
    critical_issue_ids: tuple[str, ...] = ()
    security_issue_ids: tuple[str, ...] = ()
    browser_issue_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"F13_FULL", "F14_SELECTIVE"}:
            raise RuntimeValidationError("F14_BENCHMARK_MODE_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline.baseline_id,
            "inputs_fingerprint": self.inputs.fingerprint,
            "mode": self.mode,
            "efficiency": self.efficiency.to_dict(),
            "quality": self.quality.to_dict(),
            "detected_issue_ids": list(self.detected_issue_ids),
            "critical_issue_ids": list(self.critical_issue_ids),
            "security_issue_ids": list(self.security_issue_ids),
            "browser_issue_ids": list(self.browser_issue_ids),
        }


@dataclass(frozen=True)
class F14ABComparison:
    baseline: F14BenchmarkObservation
    current: F14BenchmarkObservation
    quality_parity: Mapping[str, Any]
    detection_parity: Mapping[str, Any]
    net_context_reduction: int
    real_model_request_reduction: int
    repeated_context_reduction: int
    result: str
    comparison_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "current": self.current.to_dict(),
            "quality_parity": dict(self.quality_parity),
            "detection_parity": dict(self.detection_parity),
            "net_context_reduction": self.net_context_reduction,
            "real_model_request_reduction": self.real_model_request_reduction,
            "repeated_context_reduction": self.repeated_context_reduction,
            "result": self.result,
            "comparison_hash": self.comparison_hash,
        }


class F14ControlledABBenchmark:
    """绑定 F14-G Baseline 后比较效率与质量，不允许只比较最终 PASS/FAIL。"""

    def __init__(self, freeze: F14BaselineFreeze) -> None:
        self.freeze = freeze

    def compare(
        self,
        baseline: F14BenchmarkObservation,
        current: F14BenchmarkObservation,
    ) -> F14ABComparison:
        if baseline.baseline.baseline_id not in self.freeze.baseline_ids:
            raise RuntimeValidationError("F14_BENCHMARK_BASELINE_NOT_FROZEN")
        if current.baseline.baseline_id != baseline.baseline.baseline_id:
            raise RuntimeValidationError("F14_BENCHMARK_BASELINE_MISMATCH")
        if baseline.inputs.fingerprint != current.inputs.fingerprint:
            raise RuntimeValidationError("F14_BENCHMARK_INPUT_FINGERPRINT_MISMATCH")
        if baseline.mode != "F13_FULL" or current.mode != "F14_SELECTIVE":
            raise RuntimeValidationError("F14_BENCHMARK_MODE_SEQUENCE_INVALID")
        quality_fields = tuple(baseline.quality.__dict__)
        quality_parity = {
            field: {
                "baseline": getattr(baseline.quality, field),
                "current": getattr(current.quality, field),
                "difference": getattr(current.quality, field) - getattr(baseline.quality, field),
            }
            for field in quality_fields
        }
        detection_parity = {
            "detected_issues": sorted(set(baseline.detected_issue_ids) ^ set(current.detected_issue_ids)),
            "critical_issues": sorted(set(baseline.critical_issue_ids) ^ set(current.critical_issue_ids)),
            "security_findings": sorted(set(baseline.security_issue_ids) ^ set(current.security_issue_ids)),
            "browser_findings": sorted(set(baseline.browser_issue_ids) ^ set(current.browser_issue_ids)),
        }
        quality_ok = baseline.quality.pass_gate and current.quality.pass_gate and all(
            item["difference"] == 0 for item in quality_parity.values()
        )
        detection_ok = all(not value for value in detection_parity.values())
        context_reduction = baseline.efficiency.formal_context_bytes - current.efficiency.net_context_bytes
        request_reduction = baseline.efficiency.real_model_requests - current.efficiency.real_model_requests
        repeated_reduction = baseline.efficiency.repeated_context_bytes - current.efficiency.repeated_context_bytes
        result = "PASS" if quality_ok and detection_ok and context_reduction > 0 else "FAIL"
        payload = {
            "baseline": baseline.to_dict(),
            "current": current.to_dict(),
            "quality_parity": quality_parity,
            "detection_parity": detection_parity,
            "net_context_reduction": context_reduction,
            "real_model_request_reduction": request_reduction,
            "repeated_context_reduction": repeated_reduction,
            "result": result,
        }
        return F14ABComparison(
            baseline=baseline,
            current=current,
            quality_parity=quality_parity,
            detection_parity=detection_parity,
            net_context_reduction=context_reduction,
            real_model_request_reduction=request_reduction,
            repeated_context_reduction=repeated_reduction,
            result=result,
            comparison_hash=_hash(payload),
        )


__all__ = [
    "F14ABComparison",
    "F14BenchmarkInputs",
    "F14BenchmarkObservation",
    "F14ControlledABBenchmark",
    "F14EfficiencyMetrics",
    "F14QualityMetrics",
]
