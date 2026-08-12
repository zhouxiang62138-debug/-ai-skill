"""F14-B Minimal Telemetry：只记录可重放的计数和安全元数据。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..session_store import SessionStore, stable_id, utc_now


# 保持 F14-B 持久化 schema 号向后兼容；F14-G 字段通过 additive payload 扩展。
TELEMETRY_SCHEMA_VERSION = 1
TELEMETRY_CONTRACT_VERSION = 2
EXECUTION_TYPES = frozenset(
    {"python_only", "llm", "perception", "reused_deterministic", "rollover"}
)
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_.:/ -]{0,128}$")


def _counter_template() -> dict[str, int]:
    return {
        "file_reads": 0,
        "bytes_read": 0,
        "hash_operations": 0,
        "directory_scans": 0,
        "parser_runs": 0,
        "artifact_index_reads": 0,
        "artifact_index_rebuilds": 0,
        "dependency_graph_reads": 0,
        "dependency_graph_rebuilds": 0,
        "diff_index_builds": 0,
        "source_cache_hits": 0,
        "source_cache_misses": 0,
        "source_cache_invalidations": 0,
        "full_rebuilds": 0,
        "incremental_rebuilds": 0,
        "runtime_latency_ms": 0,
        # 保留旧键，便于 F14-B/F14-E 的历史调用方读取。
        "runtime_latency": 0,
    }


def _context_template() -> dict[str, Any]:
    return {
        "context_builds": 0,
        "current_f13": {"source_count": 0, "bytes": 0},
        "shadow_candidate": {"source_count": 0, "bytes": 0},
        "mandatory": {"unit_count": 0, "bytes": 0},
        "task_relevant": {"unit_count": 0, "bytes": 0},
        "on_demand": {"unit_count": 0, "bytes": 0},
        "omitted": {"unit_count": 0, "bytes": 0},
        "unknown": {"unit_count": 0},
        "reused_units": 0,
        "rebuilt_units": 0,
        "reused_bytes": 0,
        "rebuilt_bytes": 0,
        "context_reuse_ratio": 0.0,
        "candidate_reduction_ratio": 0.0,
        "fallback_f13_count": 0,
        # F14-B 兼容别名。
        "context_source_count": 0,
        "context_bytes": 0,
        # F14-F 将初始候选、扩展和最终净成本分开记录。
        "initial_selective_bytes": 0,
        "expansion_bytes": 0,
        "recovery_additional_bytes": 0,
        "net_context_bytes": 0,
        "gross_reduction_ratio": 0.0,
        "net_reduction_ratio": 0.0,
        "selective_attempts": 0,
        "selective_success": 0,
        "escalations": 0,
        "fallback_f13": 0,
        "blocks": 0,
        "formal_selective_reduction_bytes": 0,
    }


def _model_template() -> dict[str, Any]:
    return {
        "actual_model_requests": 0,
        "role_invocations": 0,
        "retries": 0,
        "fallbacks": 0,
        "perception_requests": 0,
        "rollovers": 0,
        "python_only_actions": 0,
        "real_llm_invocations": 0,
        "input_tokens": "unavailable",
        "cached_input_tokens": "unavailable",
        "output_tokens": "unavailable",
        "total_tokens": "unavailable",
        "token_status": "unavailable",
        "model_latency": "unavailable",
        "by_role": {},
        "by_phase": {},
        "python_only_ratio": 0.0,
    }


def _incremental_template() -> dict[str, int]:
    return {
        "full_rebuilds": 0,
        "incremental_rebuilds": 0,
        "units_total": 0,
        "units_reused": 0,
        "units_rebuilt": 0,
        "units_invalidated": 0,
        "bytes_reused": 0,
        "bytes_rebuilt": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "fallback_full_rebuilds": 0,
        "summary_hits": 0,
        "summary_invalidations": 0,
    }


def _quality_template() -> dict[str, int]:
    return {
        "mandatory_total": 0,
        "mandatory_covered": 0,
        "mandatory_missing": 0,
        "mandatory_unknown": 0,
        "mandatory_stale": 0,
        "mandatory_conflict": 0,
        "critical_false_omission": 0,
        "false_omission": 0,
        "false_mandatory": 0,
        "false_block": 0,
        "approval_chain_failures": 0,
        "evaluator_independence_failures": 0,
        "required_ac_total": 0,
        "required_ac_covered": 0,
        "regression_failures": 0,
        "browser_gate_failures": 0,
        "security_constraint_failures": 0,
        "privacy_constraint_failures": 0,
        "false_pass": 0,
        "false_block": 0,
        "fallback_f13_count": 0,
        "context_expansion_requests": 0,
        "context_expansion_denied": 0,
    }


def _escalation_template() -> dict[str, int]:
    return {
        "requests": 0,
        "approved": 0,
        "denied": 0,
        "unavailable": 0,
        "duplicate_requests": 0,
        "cycle_detected": 0,
        "stale_requests": 0,
        "unauthorized_requests": 0,
        "expansion_rounds": 0,
        "expansion_sources": 0,
        "expansion_bytes": 0,
        "l1_completed": 0,
        "l2_required": 0,
        "l3_required": 0,
        "fallback_f13": 0,
    }


def _increment_bucket(bucket: dict[str, Any], key: str, amount: int = 1) -> None:
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise ValueError("telemetry counter must be a non-negative integer")
    bucket[key] = int(bucket.get(key, 0)) + amount


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _model_attribution_bucket() -> dict[str, int]:
    return {
        "actual_model_requests": 0,
        "role_invocations": 0,
        "retries": 0,
        "fallbacks": 0,
        "rollovers": 0,
        "python_only_actions": 0,
    }


def _safe_label(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SAFE_LABEL.fullmatch(value):
        return None
    return value


@dataclass
class RuntimeTelemetry:
    """收集单次确定性工作范围内的安全计数。"""

    schema_version: int = TELEMETRY_SCHEMA_VERSION
    runtime_efficiency: dict[str, int] = field(default_factory=_counter_template)
    context_efficiency: dict[str, Any] = field(default_factory=_context_template)
    model_efficiency: dict[str, Any] = field(default_factory=_model_template)
    incremental_efficiency: dict[str, int] = field(default_factory=_incremental_template)
    quality: dict[str, int] = field(default_factory=_quality_template)
    context_escalation: dict[str, int] = field(default_factory=_escalation_template)
    context_duplication: dict[str, int] = field(
        default_factory=lambda: {
            "repeated_source_deliveries": 0,
            "repeated_bytes": 0,
            "unchanged_source_redelivery": 0,
            "unchanged_unit_redelivery": 0,
        }
    )
    incremental_context: dict[str, int] = field(
        default_factory=lambda: {
            "manifests_created": 0,
            "delta_created": 0,
            "reused_units": 0,
            "rebuilt_units": 0,
            "invalidated_units": 0,
            "stale_units": 0,
            "unknown_units": 0,
            "summary_hits": 0,
            "summary_invalidations": 0,
            "full_safe_fallbacks": 0,
        }
    )
    shadow_potential: dict[str, Any] = field(default_factory=dict)
    invocation_records: list[dict[str, Any]] = field(default_factory=list)
    phase_cost: dict[str, dict[str, Any]] = field(default_factory=dict)
    execution_types: dict[str, int] = field(default_factory=dict)
    cache_events: list[dict[str, Any]] = field(default_factory=list)

    def _increment(self, bucket: dict[str, int], key: str, amount: int = 1) -> None:
        if not isinstance(amount, int) or amount < 0:
            raise ValueError("telemetry counter must be a non-negative integer")
        bucket[key] = int(bucket.get(key, 0)) + amount

    def record_file_read(self, byte_count: int) -> None:
        self._increment(self.runtime_efficiency, "file_reads")
        self._increment(self.runtime_efficiency, "bytes_read", byte_count)

    def record_hash(self) -> None:
        self._increment(self.runtime_efficiency, "hash_operations")

    def record_directory_scan(self, count: int = 1) -> None:
        self._increment(self.runtime_efficiency, "directory_scans", count)

    def record_parser_run(self) -> None:
        self._increment(self.runtime_efficiency, "parser_runs")

    def record_runtime_latency(self, milliseconds: int) -> None:
        self._increment(self.runtime_efficiency, "runtime_latency", milliseconds)
        self._increment(self.runtime_efficiency, "runtime_latency_ms", milliseconds)

    def record_runtime_counter(self, name: str, amount: int = 1) -> None:
        """记录 G1 确定性运行时计数，不触发任何执行策略。"""

        if name not in self.runtime_efficiency:
            raise ValueError("unknown runtime telemetry counter")
        self._increment(self.runtime_efficiency, name, amount)

    def record_context(self, source_count: int, context_bytes: int) -> None:
        self._increment(self.context_efficiency, "context_builds")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (source_count, context_bytes)):
            raise ValueError("context values must be non-negative integers")
        self.context_efficiency["current_f13"]["source_count"] += source_count
        self.context_efficiency["current_f13"]["bytes"] += context_bytes
        self._increment(self.context_efficiency, "context_source_count", source_count)
        self._increment(self.context_efficiency, "context_bytes", context_bytes)

    def record_context_build(
        self,
        *,
        current_source_count: int,
        current_bytes: int,
        candidate_source_count: int = 0,
        candidate_bytes: int = 0,
        mandatory_units: int = 0,
        mandatory_bytes: int = 0,
        task_relevant_units: int = 0,
        task_relevant_bytes: int = 0,
        on_demand_units: int = 0,
        on_demand_bytes: int = 0,
        omitted_units: int = 0,
        omitted_bytes: int = 0,
        unknown_units: int = 0,
        reused_units: int = 0,
        rebuilt_units: int = 0,
        reused_bytes: int = 0,
        rebuilt_bytes: int = 0,
        fallback_f13: bool = False,
    ) -> None:
        """记录 F13 正式 Context 与 Shadow Candidate 的并列基线。"""

        values = {
            "current_source_count": current_source_count,
            "current_bytes": current_bytes,
            "candidate_source_count": candidate_source_count,
            "candidate_bytes": candidate_bytes,
            "mandatory_units": mandatory_units,
            "mandatory_bytes": mandatory_bytes,
            "task_relevant_units": task_relevant_units,
            "task_relevant_bytes": task_relevant_bytes,
            "on_demand_units": on_demand_units,
            "on_demand_bytes": on_demand_bytes,
            "omitted_units": omitted_units,
            "omitted_bytes": omitted_bytes,
            "unknown_units": unknown_units,
            "reused_units": reused_units,
            "rebuilt_units": rebuilt_units,
            "reused_bytes": reused_bytes,
            "rebuilt_bytes": rebuilt_bytes,
        }
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values.values()):
            raise ValueError("context telemetry values must be non-negative integers")
        self._increment(self.context_efficiency, "context_builds")
        current = self.context_efficiency["current_f13"]
        current["source_count"] += current_source_count
        current["bytes"] += current_bytes
        candidate = self.context_efficiency["shadow_candidate"]
        candidate["source_count"] += candidate_source_count
        candidate["bytes"] += candidate_bytes
        for key, units, bytes_value in (
            ("mandatory", mandatory_units, mandatory_bytes),
            ("task_relevant", task_relevant_units, task_relevant_bytes),
            ("on_demand", on_demand_units, on_demand_bytes),
            ("omitted", omitted_units, omitted_bytes),
        ):
            self.context_efficiency[key]["unit_count"] += units
            self.context_efficiency[key]["bytes"] += bytes_value
        self.context_efficiency["unknown"]["unit_count"] += unknown_units
        for key, amount in (
            ("reused_units", reused_units),
            ("rebuilt_units", rebuilt_units),
            ("reused_bytes", reused_bytes),
            ("rebuilt_bytes", rebuilt_bytes),
        ):
            self._increment(self.context_efficiency, key, amount)
        self.context_efficiency["candidate_reduction_ratio"] = _ratio(
            max(0, current_bytes - candidate_bytes), current_bytes
        )
        if fallback_f13:
            self._increment(self.context_efficiency, "fallback_f13_count")

    def record_selective_delivery(
        self,
        *,
        f13_full_bytes: int,
        initial_selective_bytes: int,
        expansion_bytes: int = 0,
        recovery_additional_bytes: int = 0,
        result: str,
    ) -> None:
        """记录 F14-F 的净 Context 成本，不把候选节省冒充最终节省。"""

        values = (f13_full_bytes, initial_selective_bytes, expansion_bytes, recovery_additional_bytes)
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            raise ValueError("selective delivery values must be non-negative integers")
        if result not in {"f14_selective_canary", "FALLBACK_F13", "BLOCKED"}:
            raise ValueError("unknown selective delivery result")
        net = initial_selective_bytes + expansion_bytes + recovery_additional_bytes
        context = self.context_efficiency
        self._increment(context, "selective_attempts")
        self._increment(context, "initial_selective_bytes", initial_selective_bytes)
        self._increment(context, "expansion_bytes", expansion_bytes)
        self._increment(context, "recovery_additional_bytes", recovery_additional_bytes)
        self._increment(context, "net_context_bytes", net)
        self._increment(context, "formal_selective_reduction_bytes", max(0, f13_full_bytes - net))
        if result == "f14_selective_canary":
            self._increment(context, "selective_success")
        elif result == "FALLBACK_F13":
            self._increment(context, "fallback_f13")
        else:
            self._increment(context, "blocks")
        context["gross_reduction_ratio"] = (
            max(0, f13_full_bytes - initial_selective_bytes) / f13_full_bytes
            if f13_full_bytes else 0.0
        )
        context["net_reduction_ratio"] = (
            max(0, f13_full_bytes - net) / f13_full_bytes
            if f13_full_bytes else 0.0
        )

    def record_invocation_gate(self, *, result: str, role: str | None = None, phase: str | None = None) -> None:
        """记录 Gate 生命周期；Python-only 不等于删除审计记录。"""

        if result not in {"PYTHON_ONLY", "LLM_REQUIRED", "BLOCKED"}:
            raise ValueError("unknown invocation gate result")
        self.execution_types[f"invocation_gate:{result}"] = self.execution_types.get(
            f"invocation_gate:{result}", 0
        ) + 1
        # Gate 决定只是生命周期记录；只有真正到达模型适配器边界时，才计入 actual_model_requests。
        bucket = self.model_efficiency["by_role"].setdefault(role or "unknown", _model_attribution_bucket())
        phase_bucket = self.model_efficiency["by_phase"].setdefault(phase or "unknown", _model_attribution_bucket())
        for target in (bucket, phase_bucket):
            if result == "PYTHON_ONLY":
                target["python_only_actions"] += 1
            elif result == "LLM_REQUIRED":
                target["actual_model_requests"] += 1
        total = self.model_efficiency["actual_model_requests"] + self.model_efficiency["python_only_actions"]
        self.model_efficiency["python_only_ratio"] = (
            self.model_efficiency["python_only_actions"] / total if total else 0.0
        )

    def record_shadow_potential(
        self,
        *,
        formal_context_bytes: int,
        candidate_context_bytes: int,
        mandatory_complete: bool,
        authority_verified: bool,
        unknown_count: int,
        escalation_needed: bool,
        predicted_level: str,
    ) -> None:
        """只记录 Shadow 推演结果，绝不把 Candidate 变成正式输入。"""

        if not isinstance(predicted_level, str) or not _SAFE_LABEL.fullmatch(predicted_level):
            predicted_level = "unknown"
        for value in (formal_context_bytes, candidate_context_bytes, unknown_count):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("shadow telemetry values must be non-negative integers")
        self.shadow_potential = {
            "formal_context_bytes": formal_context_bytes,
            "candidate_context_bytes": candidate_context_bytes,
            "candidate_reduction_ratio": _ratio(
                max(0, formal_context_bytes - candidate_context_bytes), formal_context_bytes
            ),
            "mandatory_complete": bool(mandatory_complete),
            "authority_verified": bool(authority_verified),
            "unknown_count": unknown_count,
            "escalation_needed": bool(escalation_needed),
            "predicted_level": predicted_level,
        }

    def record_invocation(
        self,
        execution_type: str,
        *,
        real_llm_invocation: bool = False,
        actual_model_request: bool | None = None,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        model_latency: int | None = None,
    ) -> None:
        if execution_type not in EXECUTION_TYPES:
            raise ValueError("unknown execution_type")
        self.execution_types[execution_type] = (
            self.execution_types.get(execution_type, 0) + 1
        )
        if actual_model_request is None:
            actual_model_request = real_llm_invocation
        if actual_model_request:
            self._increment(self.model_efficiency, "actual_model_requests")
        if real_llm_invocation:
            self._increment(self.model_efficiency, "real_llm_invocations")
        for name, value in (
            ("input_tokens", input_tokens),
            ("cached_input_tokens", cached_input_tokens),
            ("output_tokens", output_tokens),
            ("model_latency", model_latency),
        ):
            if value is not None:
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{name} must be a non-negative integer")
                previous = self.model_efficiency.get(name)
                self.model_efficiency[name] = value + previous if isinstance(previous, int) else value
        self._refresh_token_total()

    def _refresh_token_total(self) -> None:
        values = (
            self.model_efficiency.get("input_tokens"),
            self.model_efficiency.get("cached_input_tokens"),
            self.model_efficiency.get("output_tokens"),
        )
        if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
            self.model_efficiency["total_tokens"] = sum(values)
            self.model_efficiency["token_status"] = "actual"
        elif any(value != "unavailable" for value in values):
            self.model_efficiency["total_tokens"] = "unavailable"
            self.model_efficiency["token_status"] = "estimated"
        else:
            self.model_efficiency["total_tokens"] = "unavailable"
            self.model_efficiency["token_status"] = "unavailable"

    def record_token_usage(
        self,
        *,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """记录 Host 明确提供的 token usage，不增加 Invocation 计数。"""

        for name, value in (
            ("input_tokens", input_tokens),
            ("cached_input_tokens", cached_input_tokens),
            ("output_tokens", output_tokens),
        ):
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("token usage must be a non-negative integer")
            previous = self.model_efficiency.get(name)
            self.model_efficiency[name] = value + previous if isinstance(previous, int) else value
        self._refresh_token_total()

    def record_model_request(
        self,
        *,
        role: str,
        phase: str,
        invocation_reason: str,
        project_revision: int,
        task_id: str,
        context_manifest_hash: str,
        actual_model_request: bool,
        execution_type: str = "llm",
        invocation_id: str | None = None,
        real_model: bool | None = None,
        context_bytes: int = 0,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        """在真实模型适配器边界保存安全的 Role/Phase 归因。"""

        safe_values = (role, phase, invocation_reason, task_id, context_manifest_hash)
        if any(not isinstance(value, str) or not value or not _SAFE_LABEL.fullmatch(value) for value in safe_values):
            raise ValueError("model attribution contains an unsafe label")
        if not isinstance(project_revision, int) or project_revision < 0:
            raise ValueError("project_revision must be non-negative")
        if not isinstance(context_bytes, int) or isinstance(context_bytes, bool) or context_bytes < 0:
            raise ValueError("context_bytes must be a non-negative integer")
        role_bucket = self.model_efficiency["by_role"].setdefault(role, _model_attribution_bucket())
        phase_bucket = self.model_efficiency["by_phase"].setdefault(phase, _model_attribution_bucket())
        for bucket in (role_bucket, phase_bucket):
            bucket["role_invocations"] += 1
            if actual_model_request:
                bucket["actual_model_requests"] += 1
        self.model_efficiency["role_invocations"] += 1
        if execution_type == "perception":
            self.model_efficiency["perception_requests"] += int(actual_model_request)
        elif execution_type == "python_only":
            self.model_efficiency["python_only_actions"] += 1
        is_real_llm = (
            actual_model_request
            and execution_type in {"llm", "perception"}
            and real_model is not False
        )
        self.record_invocation(
            execution_type,
            real_llm_invocation=is_real_llm,
            actual_model_request=actual_model_request,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
        )
        token_values = (input_tokens, cached_input_tokens, output_tokens)
        phase_tokens: int | str = (
            sum(token_values) if all(isinstance(value, int) for value in token_values) else "unavailable"
        )
        self.record_phase_cost(
            phase,
            role=role,
            context_bytes=context_bytes,
            actual_tokens=phase_tokens,
            real_model_requests=int(is_real_llm),
        )
        record = {
            "record_type": "real_model_request" if actual_model_request else "role_thread_dispatch",
            "role": role,
            "phase": phase,
            "invocation_reason": invocation_reason,
            "project_revision": project_revision,
            "task_id": task_id,
            "context_manifest_hash": context_manifest_hash,
            "execution_type": execution_type,
            "model_kind": "real_model" if is_real_llm else "controlled_or_synthetic",
        }
        if invocation_id is not None and isinstance(invocation_id, str) and _SAFE_LABEL.fullmatch(invocation_id):
            record["invocation_id"] = invocation_id
        if len(self.invocation_records) < 512:
            self.invocation_records.append(record)

    def record_model_event(self, event: str, *, role: str | None = None, phase: str | None = None) -> None:
        """记录 retry/fallback/rollover/python-only 等非模型请求事件。"""

        if event not in {"retry", "fallback", "rollover", "python_only"}:
            raise ValueError("unknown model event")
        self._increment(self.model_efficiency, {
            "retry": "retries",
            "fallback": "fallbacks",
            "rollover": "rollovers",
            "python_only": "python_only_actions",
        }[event])
        for value in (role, phase):
            if value is not None and (not isinstance(value, str) or not _SAFE_LABEL.fullmatch(value)):
                return
        counter_name = {
            "retry": "retries",
            "fallback": "fallbacks",
            "rollover": "rollovers",
            "python_only": "python_only_actions",
        }[event]
        if role:
            bucket = self.model_efficiency["by_role"].setdefault(role, _model_attribution_bucket())
            bucket[counter_name] += 1
        if phase:
            bucket = self.model_efficiency["by_phase"].setdefault(phase, _model_attribution_bucket())
            bucket[counter_name] += 1

    def record_phase_cost(
        self,
        phase: str,
        *,
        role: str | None = None,
        context_bytes: int = 0,
        actual_tokens: int | str = "unavailable",
        real_model_requests: int = 0,
        candidate_reduction: float = 0.0,
    ) -> None:
        if not isinstance(phase, str) or not _SAFE_LABEL.fullmatch(phase):
            raise ValueError("phase must be a safe label")
        if not isinstance(context_bytes, int) or context_bytes < 0 or not isinstance(real_model_requests, int) or real_model_requests < 0:
            raise ValueError("phase cost values are invalid")
        existing = self.phase_cost.get(phase, {})
        previous_tokens = existing.get("actual_tokens") if isinstance(existing, dict) else None
        if isinstance(previous_tokens, int) and isinstance(actual_tokens, int):
            resolved_tokens: int | str = previous_tokens + actual_tokens
        elif isinstance(actual_tokens, int) and previous_tokens in (None, "unavailable"):
            resolved_tokens = actual_tokens
        else:
            resolved_tokens = actual_tokens if actual_tokens in {"actual", "estimated", "unavailable"} else "unavailable"
        self.phase_cost[phase] = {
            "role": role if isinstance(role, str) and _SAFE_LABEL.fullmatch(role) else existing.get("role"),
            "real_model_requests": int(existing.get("real_model_requests", 0)) + real_model_requests,
            "context_bytes": int(existing.get("context_bytes", 0)) + context_bytes,
            "actual_tokens": resolved_tokens,
            "candidate_reduction": max(float(existing.get("candidate_reduction", 0.0)), max(0.0, min(1.0, float(candidate_reduction)))),
        }

    def record_quality(self, **values: int) -> None:
        self._record_counter_group(self.quality, values, "quality")

    def record_context_escalation(self, **values: int) -> None:
        self._record_counter_group(self.context_escalation, values, "context escalation")

    def record_context_duplication(self, **values: int) -> None:
        self._record_counter_group(self.context_duplication, values, "context duplication")

    def record_incremental_context(self, **values: int) -> None:
        self._record_counter_group(self.incremental_context, values, "incremental context")

    @staticmethod
    def _record_counter_group(bucket: dict[str, int], values: Mapping[str, int], label: str) -> None:
        for key, amount in values.items():
            if key not in bucket:
                raise ValueError(f"unknown {label} counter")
            _increment_bucket(bucket, key, amount)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RuntimeTelemetry":
        """从受控持久化快照恢复 Telemetry；损坏输入只在调用方显式处理时失败。"""

        if not isinstance(value, Mapping):
            raise ValueError("telemetry snapshot must be a mapping")
        telemetry = cls(schema_version=int(value.get("schema_version", TELEMETRY_SCHEMA_VERSION)))
        for name in (
            "runtime_efficiency",
            "context_efficiency",
            "model_efficiency",
            "incremental_efficiency",
            "quality",
            "context_escalation",
            "context_duplication",
            "incremental_context",
            "shadow_potential",
            "phase_cost",
            "execution_types",
        ):
            raw = value.get(name)
            if isinstance(raw, Mapping):
                target = getattr(telemetry, name)
                if isinstance(target, dict):
                    target.update(dict(raw))
        records = value.get("invocation_records")
        if isinstance(records, list):
            telemetry.invocation_records = [dict(item) for item in records if isinstance(item, Mapping)][:512]
        events = value.get("cache_events")
        if isinstance(events, list):
            telemetry.cache_events = [dict(item) for item in events if isinstance(item, Mapping)][:128]
        return telemetry

    def merge(self, other: "RuntimeTelemetry") -> "RuntimeTelemetry":
        """合并独立记录，供 Benchmark 聚合；不改变任何正式 Runtime 状态。"""

        if not isinstance(other, RuntimeTelemetry):
            raise ValueError("other must be RuntimeTelemetry")
        result = RuntimeTelemetry.from_mapping(self.to_dict())
        for name in (
            "runtime_efficiency",
            "incremental_efficiency",
            "quality",
            "context_escalation",
            "context_duplication",
            "incremental_context",
        ):
            left = getattr(result, name)
            right = getattr(other, name)
            for key, value in right.items():
                if isinstance(value, int):
                    left[key] = int(left.get(key, 0)) + value
        for group in ("current_f13", "shadow_candidate", "mandatory", "task_relevant", "on_demand", "omitted", "unknown"):
            left_group = result.context_efficiency[group]
            right_group = other.context_efficiency[group]
            for key, value in right_group.items():
                if isinstance(value, int):
                    left_group[key] = int(left_group.get(key, 0)) + value
        for key in ("context_builds", "reused_units", "rebuilt_units", "reused_bytes", "rebuilt_bytes", "fallback_f13_count", "context_source_count", "context_bytes"):
            result.context_efficiency[key] = int(result.context_efficiency.get(key, 0)) + int(other.context_efficiency.get(key, 0))
        for key in ("actual_model_requests", "role_invocations", "retries", "fallbacks", "perception_requests", "rollovers", "python_only_actions", "real_llm_invocations"):
            result.model_efficiency[key] = int(result.model_efficiency.get(key, 0)) + int(other.model_efficiency.get(key, 0))
        for key in (
            "initial_selective_bytes", "expansion_bytes",
            "recovery_additional_bytes", "net_context_bytes", "selective_attempts",
            "selective_success", "escalations", "fallback_f13", "blocks",
            "formal_selective_reduction_bytes",
        ):
            result.context_efficiency[key] = int(result.context_efficiency.get(key, 0)) + int(
                other.context_efficiency.get(key, 0)
            )
        if result.context_efficiency["current_f13"]["bytes"]:
            current_bytes = result.context_efficiency["current_f13"]["bytes"]
            initial = result.context_efficiency["initial_selective_bytes"]
            net = result.context_efficiency["net_context_bytes"]
            result.context_efficiency["gross_reduction_ratio"] = max(0, current_bytes - initial) / current_bytes
            result.context_efficiency["net_reduction_ratio"] = max(0, current_bytes - net) / current_bytes
        for group_name in ("by_role", "by_phase"):
            left_group = result.model_efficiency[group_name]
            for label, right_bucket in other.model_efficiency[group_name].items():
                left_bucket = left_group.setdefault(label, _model_attribution_bucket())
                for key, value in right_bucket.items():
                    if isinstance(value, int):
                        left_bucket[key] = int(left_bucket.get(key, 0)) + value
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "model_latency"):
            left_value = result.model_efficiency.get(key)
            right_value = other.model_efficiency.get(key)
            if isinstance(right_value, int):
                result.model_efficiency[key] = right_value + left_value if isinstance(left_value, int) else right_value
        total_model_actions = result.model_efficiency["actual_model_requests"] + result.model_efficiency["python_only_actions"]
        result.model_efficiency["python_only_ratio"] = (
            result.model_efficiency["python_only_actions"] / total_model_actions
            if total_model_actions else 0.0
        )
        result._refresh_token_total()
        for phase, value in other.phase_cost.items():
            result.phase_cost[phase] = dict(value)
        result.invocation_records = (result.invocation_records + other.invocation_records)[:512]
        result.cache_events = (result.cache_events + other.cache_events)[:128]
        return result

    def record_cache_event(
        self,
        hit_or_miss: str,
        *,
        invalidation_reason: str | None = None,
        fallback_read: bool = False,
        parser_version: str = "",
    ) -> None:
        if hit_or_miss not in {"hit", "miss"}:
            raise ValueError("cache event must be hit or miss")
        self._increment(self.incremental_efficiency, "cache_hits" if hit_or_miss == "hit" else "cache_misses")
        self._increment(
            self.runtime_efficiency,
            "source_cache_hits" if hit_or_miss == "hit" else "source_cache_misses",
        )
        if invalidation_reason:
            self._increment(self.runtime_efficiency, "source_cache_invalidations")
        if not _SAFE_LABEL.fullmatch(parser_version):
            parser_version = ""
        if len(self.cache_events) < 128:
            self.cache_events.append(
                {
                    "hit_or_miss": hit_or_miss,
                    "invalidation_reason": _safe_label(invalidation_reason),
                    "fallback_read": bool(fallback_read),
                    "parser_version": parser_version,
                }
            )

    def record_incremental_build(
        self,
        *,
        full_rebuild: bool,
        units_total: int,
        units_reused: int = 0,
        units_rebuilt: int = 0,
        units_invalidated: int = 0,
        bytes_reused: int = 0,
        bytes_rebuilt: int = 0,
        fallback_full_rebuild: bool = False,
    ) -> None:
        values = {
            "units_total": units_total,
            "units_reused": units_reused,
            "units_rebuilt": units_rebuilt,
            "units_invalidated": units_invalidated,
            "bytes_reused": bytes_reused,
            "bytes_rebuilt": bytes_rebuilt,
        }
        if any(not isinstance(value, int) or value < 0 for value in values.values()):
            raise ValueError("incremental counters must be non-negative integers")
        self._increment(
            self.incremental_efficiency,
            "full_rebuilds" if full_rebuild else "incremental_rebuilds",
        )
        self._increment(
            self.runtime_efficiency,
            "full_rebuilds" if full_rebuild else "incremental_rebuilds",
        )
        for name, value in values.items():
            self._increment(self.incremental_efficiency, name, value)
        self._increment(self.incremental_context, "rebuilt_units", units_rebuilt)
        self._increment(self.incremental_context, "reused_units", units_reused)
        self._increment(self.incremental_context, "invalidated_units", units_invalidated)
        if fallback_full_rebuild:
            self._increment(self.incremental_efficiency, "fallback_full_rebuilds")
            self._increment(self.incremental_context, "full_safe_fallbacks")

    def record_summary_hit(self) -> None:
        self._increment(self.incremental_efficiency, "summary_hits")
        self._increment(self.incremental_context, "summary_hits")

    def record_summary_invalidation(self) -> None:
        self._increment(self.incremental_efficiency, "summary_invalidations")
        self._increment(self.incremental_context, "summary_invalidations")

    def incremental_ratios(self) -> dict[str, float]:
        values = self.incremental_efficiency
        builds = values["full_rebuilds"] + values["incremental_rebuilds"]
        units = values["units_total"]
        return {
            "context_reuse_ratio": values["units_reused"] / units if units else 0.0,
            "incremental_rebuild_ratio": values["incremental_rebuilds"] / builds if builds else 0.0,
            "fallback_rate": values["fallback_full_rebuilds"] / builds if builds else 0.0,
        }

    def context_ratios(self) -> dict[str, float]:
        values = self.context_efficiency
        current_bytes = int(values["current_f13"]["bytes"])
        candidate_bytes = int(values["shadow_candidate"]["bytes"])
        rebuilt = int(values["rebuilt_units"])
        unit_total = int(values["reused_units"]) + rebuilt
        return {
            "context_reuse_ratio": _ratio(int(values["reused_units"]), unit_total),
            "candidate_reduction_ratio": _ratio(
                max(0, current_bytes - candidate_bytes), current_bytes
            ),
            "rebuild_ratio": _ratio(rebuilt, unit_total),
        }

    def to_dict(self) -> dict[str, Any]:
        """返回不包含原始输出、日志或 Secret 的可持久化指标。"""

        return {
            "schema_version": self.schema_version,
            "runtime_efficiency": dict(self.runtime_efficiency),
            "context_efficiency": dict(self.context_efficiency),
            "model_efficiency": dict(self.model_efficiency),
            "incremental_efficiency": dict(self.incremental_efficiency),
            "quality": dict(self.quality),
            "context_escalation": dict(self.context_escalation),
            "incremental_context": dict(self.incremental_context),
            "context_duplication": dict(self.context_duplication),
            "shadow_potential": dict(self.shadow_potential),
            "phase_cost": dict(self.phase_cost),
            "invocation_records": list(self.invocation_records),
            "incremental_ratios": self.incremental_ratios(),
            "context_ratios": self.context_ratios(),
            "contract_version": TELEMETRY_CONTRACT_VERSION,
            "execution_types": dict(self.execution_types),
            "cache_events": list(self.cache_events),
        }

    def persist(
        self,
        store: SessionStore,
        *,
        session_id: str,
        project_id: str,
        project_revision: int = 0,
        role: str | None = None,
        phase: str | None = None,
        execution_type: str = "python_only",
        idempotency_key: str,
    ) -> bool:
        """尽力持久化 Telemetry；失败不能打断业务流程。"""

        if execution_type not in EXECUTION_TYPES:
            return False
        safe_role = _safe_label(role)
        safe_phase = _safe_label(phase)
        if project_revision < 0 or not idempotency_key:
            return False
        payload = self.to_dict()
        try:
            with store.transaction(immediate=True) as connection:
                existing = connection.execute(
                    """
                    SELECT telemetry_id FROM f14_telemetry
                    WHERE session_id=? AND idempotency_key=?
                    """,
                    (session_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    return True
                connection.execute(
                    """
                    INSERT INTO f14_telemetry(
                        telemetry_id, session_id, project_id, project_revision,
                        role, phase, execution_type, schema_version,
                        runtime_json, context_json, model_json,
                        details_json,
                        created_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stable_id("f14-telemetry", session_id, idempotency_key),
                        session_id,
                        project_id,
                        project_revision,
                        safe_role,
                        safe_phase,
                        execution_type,
                        self.schema_version,
                        json.dumps(
                            {
                                "runtime_efficiency": payload["runtime_efficiency"],
                                "cache_events": payload["cache_events"],
                                "incremental_efficiency": payload["incremental_efficiency"],
                                "incremental_ratios": payload["incremental_ratios"],
                                "incremental_context": payload["incremental_context"],
                                "context_duplication": payload["context_duplication"],
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            {
                                "context_efficiency": payload["context_efficiency"],
                                "context_ratios": payload["context_ratios"],
                                "context_escalation": payload["context_escalation"],
                                "shadow_potential": payload["shadow_potential"],
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            {
                                "model_efficiency": payload["model_efficiency"],
                                "execution_types": payload["execution_types"],
                                "quality": payload["quality"],
                                "phase_cost": payload["phase_cost"],
                                "invocation_records": payload["invocation_records"],
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        utc_now(),
                        idempotency_key,
                    ),
                )
            return True
        except Exception:
            return False


def timed(telemetry: RuntimeTelemetry):
    """返回一个只累计毫秒数的轻量计时器。"""

    started = time.perf_counter()

    def finish() -> None:
        elapsed = max(0, int((time.perf_counter() - started) * 1000))
        telemetry.record_runtime_latency(elapsed)

    return finish


__all__ = [
    "EXECUTION_TYPES",
    "TELEMETRY_CONTRACT_VERSION",
    "TELEMETRY_SCHEMA_VERSION",
    "RuntimeTelemetry",
    "timed",
]
