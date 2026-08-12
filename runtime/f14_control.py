"""F14-F 控制面：Feature Flag、Selective Context 门禁与净成本计算。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from runtime.errors import RuntimeValidationError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.deterministic.benchmark import BaselineSnapshot


F13_FULL = "f13_full"
F14_SELECTIVE = "f14_selective_canary"
DELIVERY_BLOCKED = "blocked"
DELIVERY_MODES = frozenset({F13_FULL, F14_SELECTIVE})
DECISION_RESULTS = frozenset({F14_SELECTIVE, "FALLBACK_F13", "BLOCKED"})
BASELINE_IDS = tuple(f"F14-G-BASELINE-{index:03d}" for index in range(1, 6))
ROLLOUT_STATES = frozenset(
    {
        "IMPLEMENTED",
        "CONTROLLED_QUALIFIED",
        "REAL_MODEL_QUALIFIED",
        "GLOBAL_READY",
        "GLOBAL_ENABLED",
        "FALLBACK_F13",
    }
)
ROLLOUT_MODES = frozenset({"controlled", "global", "fallback"})
ROLLOUT_TRANSITIONS: dict[str, frozenset[str]] = {
    "IMPLEMENTED": frozenset({"CONTROLLED_QUALIFIED", "FALLBACK_F13"}),
    "CONTROLLED_QUALIFIED": frozenset({"REAL_MODEL_QUALIFIED", "FALLBACK_F13"}),
    "REAL_MODEL_QUALIFIED": frozenset({"GLOBAL_READY", "FALLBACK_F13"}),
    "GLOBAL_READY": frozenset({"GLOBAL_ENABLED", "FALLBACK_F13"}),
    "GLOBAL_ENABLED": frozenset({"FALLBACK_F13"}),
    "FALLBACK_F13": frozenset({"IMPLEMENTED", "CONTROLLED_QUALIFIED"}),
}
REQUIRED_QUALIFICATION_EVIDENCE: dict[str, tuple[str, ...]] = {
    "CONTROLLED_QUALIFIED": ("controlled",),
    "REAL_MODEL_QUALIFIED": ("controlled", "real_model"),
    "GLOBAL_READY": ("controlled", "real_model", "real_browser", "evaluator_selective"),
    "GLOBAL_ENABLED": (
        "controlled",
        "real_model",
        "real_browser",
        "evaluator_selective",
        "quality_parity",
        "fault_injection",
        "fallback",
    ),
}
FEATURE_QUALIFICATION_EVIDENCE: dict[str, tuple[str, ...]] = {
    "selective_context": ("controlled",),
    "invocation_gate": ("controlled", "fault_injection"),
    "evaluator_selective_context": (
        "evaluator_selective",
        "quality_parity",
        "real_model",
        "real_browser",
    ),
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _non_negative(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeValidationError(f"F14_{name.upper()}_INVALID")
    return value


def _tuple_strings(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise RuntimeValidationError(f"F14_{name.upper()}_INVALID")
    result = tuple(sorted({item for item in value if isinstance(item, str) and item}))
    if len(result) != len(tuple(value)):
        raise RuntimeValidationError(f"F14_{name.upper()}_INVALID")
    return result


@dataclass(frozen=True)
class F14RolloutPolicy:
    """F14 的资格晋级与紧急回退状态机。"""

    mode: str = "controlled"
    qualification_status: str = "IMPLEMENTED"
    fallback_mode: str = F13_FULL
    automatic_fallback: bool = True
    global_enabled: bool = False
    manual_kill_switch: bool = True
    kill_switch_active: bool = False
    per_project_override: bool = True
    per_role_override: bool = True
    per_phase_override: bool = True
    disabled_projects: tuple[str, ...] = ()
    disabled_roles: tuple[str, ...] = ()
    disabled_phases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ROLLOUT_MODES or self.qualification_status not in ROLLOUT_STATES:
            raise RuntimeValidationError("F14_ROLLOUT_STATE_INVALID")
        if self.fallback_mode != F13_FULL or not isinstance(self.automatic_fallback, bool):
            raise RuntimeValidationError("F14_ROLLOUT_FALLBACK_INVALID")
        for name in (
            "global_enabled",
            "manual_kill_switch",
            "kill_switch_active",
            "per_project_override",
            "per_role_override",
            "per_phase_override",
        ):
            if not isinstance(getattr(self, name), bool):
                raise RuntimeValidationError("F14_ROLLOUT_FLAG_INVALID")
        if self.global_enabled != (
            self.mode == "global" and self.qualification_status == "GLOBAL_ENABLED"
        ):
            raise RuntimeValidationError("F14_GLOBAL_ENABLEMENT_UNQUALIFIED")
        if self.kill_switch_active and not self.manual_kill_switch:
            raise RuntimeValidationError("F14_KILL_SWITCH_INVALID")
        for name in ("disabled_projects", "disabled_roles", "disabled_phases"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, str) or not item for item in values):
                raise RuntimeValidationError("F14_ROLLOUT_OVERRIDE_INVALID")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "F14RolloutPolicy":
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise RuntimeValidationError("F14_ROLLOUT_CONFIG_INVALID")
        overrides = value.get("overrides", {})
        if not isinstance(overrides, Mapping):
            raise RuntimeValidationError("F14_ROLLOUT_CONFIG_INVALID")
        return cls(
            mode=str(value.get("mode", "controlled")),
            qualification_status=str(value.get("qualification_status", "IMPLEMENTED")),
            fallback_mode=str(value.get("fallback_mode", F13_FULL)),
            automatic_fallback=value.get("automatic_fallback", True),
            global_enabled=value.get("global_enabled", False),
            manual_kill_switch=value.get("manual_kill_switch", True),
            kill_switch_active=value.get("kill_switch_active", False),
            per_project_override=value.get("per_project_override", True),
            per_role_override=value.get("per_role_override", True),
            per_phase_override=value.get("per_phase_override", True),
            disabled_projects=_tuple_strings(overrides.get("disabled_projects", ()), "disabled_projects"),
            disabled_roles=_tuple_strings(overrides.get("disabled_roles", ()), "disabled_roles"),
            disabled_phases=_tuple_strings(overrides.get("disabled_phases", ()), "disabled_phases"),
        )

    def can_transition(self, target: str, evidence: Mapping[str, Any] | None = None) -> bool:
        """只允许按资格证据逐级晋升；回退始终是可用的安全路径。"""

        if target not in ROLLOUT_STATES:
            return False
        if target not in ROLLOUT_TRANSITIONS.get(self.qualification_status, frozenset()):
            return False
        required = REQUIRED_QUALIFICATION_EVIDENCE.get(target, ())
        return all((evidence or {}).get(key) == "PASS" for key in required)

    def assert_transition(self, target: str, evidence: Mapping[str, Any] | None = None) -> None:
        if not self.can_transition(target, evidence):
            raise RuntimeValidationError("F14_ROLLOUT_PROMOTION_DENIED")

    @property
    def effective_delivery_enabled(self) -> bool:
        return not (self.kill_switch_active and self.manual_kill_switch)

    def is_disabled_for(self, *, role: str, phase: str, project_id: str | None) -> bool:
        return (
            (self.per_project_override and project_id is not None and project_id in self.disabled_projects)
            or (self.per_role_override and role in self.disabled_roles)
            or (self.per_phase_override and phase in self.disabled_phases)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "qualification_status": self.qualification_status,
            "fallback_mode": self.fallback_mode,
            "automatic_fallback": self.automatic_fallback,
            "global_enabled": self.global_enabled,
            "manual_kill_switch": self.manual_kill_switch,
            "kill_switch_active": self.kill_switch_active,
            "per_project_override": self.per_project_override,
            "per_role_override": self.per_role_override,
            "per_phase_override": self.per_phase_override,
            "overrides": {
                "disabled_projects": list(self.disabled_projects),
                "disabled_roles": list(self.disabled_roles),
                "disabled_phases": list(self.disabled_phases),
            },
        }


@dataclass(frozen=True)
class F14FeatureFlags:
    """F14 的可回滚开关；默认关闭所有正式优化。"""

    context_delivery_mode: str = F13_FULL
    selective_context_enabled: bool = False
    selective_roles: tuple[str, ...] = ()
    selective_phases: tuple[str, ...] = ()
    canary_projects: tuple[str, ...] = ()
    invocation_gate_enabled: bool = False
    evaluator_selective_context: bool = False
    qualification_evidence: Mapping[str, Any] = field(default_factory=dict)
    rollout: F14RolloutPolicy = field(default_factory=F14RolloutPolicy)

    def __post_init__(self) -> None:
        if self.context_delivery_mode not in DELIVERY_MODES:
            raise RuntimeValidationError("F14_CONTEXT_DELIVERY_MODE_INVALID")
        for name in (
            "selective_context_enabled",
            "invocation_gate_enabled",
            "evaluator_selective_context",
        ):
            if not isinstance(getattr(self, name), bool):
                raise RuntimeValidationError("F14_FEATURE_FLAG_INVALID")
        for name in ("selective_roles", "selective_phases", "canary_projects"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                not isinstance(item, str) or not item for item in values
            ):
                raise RuntimeValidationError("F14_FEATURE_FLAG_INVALID")
        if not isinstance(self.rollout, F14RolloutPolicy):
            raise RuntimeValidationError("F14_ROLLOUT_CONFIG_INVALID")
        if not isinstance(self.qualification_evidence, Mapping):
            raise RuntimeValidationError("F14_QUALIFICATION_EVIDENCE_INVALID")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "F14FeatureFlags":
        if not isinstance(value, Mapping):
            raise RuntimeValidationError("F14_FEATURE_FLAG_CONFIG_INVALID")
        section = value.get("f14", value)
        if not isinstance(section, Mapping):
            raise RuntimeValidationError("F14_FEATURE_FLAG_CONFIG_INVALID")
        selective = section.get("selective_context", {})
        if not isinstance(selective, Mapping):
            raise RuntimeValidationError("F14_FEATURE_FLAG_CONFIG_INVALID")
        gate = section.get("invocation_gate", {})
        if not isinstance(gate, Mapping):
            raise RuntimeValidationError("F14_FEATURE_FLAG_CONFIG_INVALID")
        rollout = F14RolloutPolicy.from_mapping(section.get("rollout"))
        qualification_evidence = section.get("qualification_evidence", {})
        if not isinstance(qualification_evidence, Mapping):
            raise RuntimeValidationError("F14_QUALIFICATION_EVIDENCE_INVALID")
        mode = str(section.get("context_delivery_mode", F13_FULL))
        return cls(
            context_delivery_mode=mode,
            selective_context_enabled=bool(selective.get("enabled", False)),
            selective_roles=_tuple_strings(selective.get("roles", ()), "selective_roles"),
            selective_phases=_tuple_strings(selective.get("phases", ()), "selective_phases"),
            canary_projects=_tuple_strings(selective.get("canary_projects", ()), "canary_projects"),
            invocation_gate_enabled=bool(gate.get("enabled", False)),
            evaluator_selective_context=bool(
                section.get("evaluator_selective_context", {}).get("enabled", False)
                if isinstance(section.get("evaluator_selective_context", {}), Mapping)
                else False
            ),
            qualification_evidence=dict(qualification_evidence),
            rollout=rollout,
        )

    @classmethod
    def load(cls, path: str | Path | None = None) -> "F14FeatureFlags":
        root = Path(__file__).resolve().parents[1]
        config_path = Path(path) if path else root / "config" / "f14.yaml"
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeValidationError("F14_FEATURE_FLAG_CONFIG_UNAVAILABLE") from exc
        return cls.from_mapping(raw or {})

    def _evidence_pass(self, key: str) -> bool:
        return self.qualification_evidence.get(key) == "PASS"

    def _evidence_zero(self, key: str) -> bool:
        return self.qualification_evidence.get(key) == 0

    def selective_context_qualified(self) -> bool:
        return (
            self.rollout.qualification_status not in {"IMPLEMENTED", "FALLBACK_F13"}
            and self._evidence_pass("controlled")
        )

    def invocation_gate_qualified(self) -> bool:
        return (
            self.rollout.qualification_status not in {"IMPLEMENTED", "FALLBACK_F13"}
            and self._evidence_pass("controlled")
            and self._evidence_pass("fault_injection")
            and self._evidence_zero("semantic_task_misclassified_as_python_only")
        )

    def evaluator_selective_qualified(self) -> bool:
        return (
            self.rollout.qualification_status not in {"IMPLEMENTED", "FALLBACK_F13"}
            and all(self._evidence_pass(key) for key in FEATURE_QUALIFICATION_EVIDENCE["evaluator_selective_context"])
        )

    def selective_allowed(
        self,
        *,
        role: str,
        phase: str,
        project_id: str | None = None,
        benchmark: bool = False,
        explicit_canary: bool = False,
        test_project: bool = False,
    ) -> bool:
        """只有测试项目、Benchmark 或明确 Canary 才能打开 F14 Context。"""

        if (
            not self.selective_context_enabled
            or self.context_delivery_mode != F14_SELECTIVE
            or not self.rollout.effective_delivery_enabled
            or not self.selective_context_qualified()
            or self.rollout.is_disabled_for(role=role, phase=phase, project_id=project_id)
        ):
            return False
        if role not in self.selective_roles or phase not in self.selective_phases:
            return False
        if role == "evaluator":
            if not self.evaluator_selective_context or not self.evaluator_selective_qualified():
                return False
        canary = self.rollout.global_enabled or (
            benchmark
            or explicit_canary
            or test_project
            or (project_id is not None and project_id in self.canary_projects)
        )
        return canary

    def delivery_for(self, **kwargs: Any) -> str:
        return F14_SELECTIVE if self.selective_allowed(**kwargs) else F13_FULL

    def rollback(self) -> "F14FeatureFlags":
        """返回关闭优化的新投影，不修改磁盘上的配置或历史记录。"""

        return F14FeatureFlags(
            rollout=F14RolloutPolicy(
                mode="fallback",
                qualification_status="FALLBACK_F13",
                global_enabled=False,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "f14": {
                "context_delivery_mode": self.context_delivery_mode,
                "selective_context": {
                    "enabled": self.selective_context_enabled,
                    "roles": list(self.selective_roles),
                    "phases": list(self.selective_phases),
                    "canary_projects": list(self.canary_projects),
                },
                "invocation_gate": {"enabled": self.invocation_gate_enabled},
                "evaluator_selective_context": {
                    "enabled": self.evaluator_selective_context
                },
                "qualification_evidence": dict(self.qualification_evidence),
                "rollout": self.rollout.to_dict(),
            }
        }


@dataclass(frozen=True)
class F14BaselineFreeze:
    """只读校验 F14-G 的五个冻结 Baseline，不生成或覆盖 Baseline。"""

    baselines: tuple["BaselineSnapshot", ...]
    baseline_ids: tuple[str, ...] = field(init=False)
    freeze_hash: str = field(init=False)

    def __post_init__(self) -> None:
        values = tuple(self.baselines)
        if len(values) != len(BASELINE_IDS):
            raise RuntimeValidationError("F14_G_BASELINE_SET_INCOMPLETE")
        ids = tuple(item.baseline_id for item in values)
        if set(ids) != set(BASELINE_IDS):
            raise RuntimeValidationError("F14_G_BASELINE_ID_MISMATCH")
        if len(ids) != len(set(ids)):
            raise RuntimeValidationError("F14_G_BASELINE_ID_DUPLICATE")
        if any(item.runtime_schema <= 0 for item in values):
            raise RuntimeValidationError("F14_G_BASELINE_SCHEMA_INVALID")
        object.__setattr__(self, "baselines", tuple(sorted(values, key=lambda item: item.baseline_id)))
        object.__setattr__(self, "baseline_ids", tuple(sorted(ids)))
        object.__setattr__(self, "freeze_hash", _hash([item.to_dict() for item in self.baselines]))

    @classmethod
    def from_mappings(cls, values: Iterable[Mapping[str, Any]]) -> "F14BaselineFreeze":
        from runtime.deterministic.benchmark import BaselineSnapshot

        return cls(tuple(BaselineSnapshot.from_mapping(value) for value in values))

    @classmethod
    def from_directory(cls, directory: str | Path) -> "F14BaselineFreeze":
        root = Path(directory)
        records: list[Mapping[str, Any]] = []
        for baseline_id in BASELINE_IDS:
            suffix = baseline_id.rsplit("-", 1)[-1]
            path = root / f"baseline-{suffix}.json"
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeValidationError("F14_G_BASELINE_READ_FAILED") from exc
        return cls.from_mappings(records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_ids": list(self.baseline_ids),
            "freeze_hash": self.freeze_hash,
            "baselines": [item.to_dict() for item in self.baselines],
        }


@dataclass(frozen=True)
class ContextSavings:
    """按初始 Selective、扩展和恢复成本计算真实净 Context。"""

    f13_full_bytes: int
    initial_selective_bytes: int
    expansion_bytes: int = 0
    recovery_bytes: int = 0

    def __post_init__(self) -> None:
        for name in (
            "f13_full_bytes",
            "initial_selective_bytes",
            "expansion_bytes",
            "recovery_bytes",
        ):
            _non_negative(getattr(self, name), name)

    @property
    def net_context_bytes(self) -> int:
        return self.initial_selective_bytes + self.expansion_bytes + self.recovery_bytes

    @property
    def gross_reduction_ratio(self) -> float:
        return max(0, self.f13_full_bytes - self.initial_selective_bytes) / self.f13_full_bytes if self.f13_full_bytes else 0.0

    @property
    def net_reduction_ratio(self) -> float:
        return max(0, self.f13_full_bytes - self.net_context_bytes) / self.f13_full_bytes if self.f13_full_bytes else 0.0

    @property
    def expansion_rate(self) -> float:
        return self.expansion_bytes / self.initial_selective_bytes if self.initial_selective_bytes else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "f13_full_bytes": self.f13_full_bytes,
            "initial_selective_bytes": self.initial_selective_bytes,
            "expansion_bytes": self.expansion_bytes,
            "recovery_bytes": self.recovery_bytes,
            "net_context_bytes": self.net_context_bytes,
            "gross_reduction_ratio": self.gross_reduction_ratio,
            "net_reduction_ratio": self.net_reduction_ratio,
            "expansion_rate": self.expansion_rate,
        }


def compute_context_savings(
    f13_full_bytes: int,
    initial_selective_bytes: int,
    expansion_bytes: int = 0,
    recovery_bytes: int = 0,
) -> dict[str, Any]:
    return ContextSavings(
        f13_full_bytes,
        initial_selective_bytes,
        expansion_bytes,
        recovery_bytes,
    ).to_dict()


@dataclass(frozen=True)
class SelectiveGateInput:
    """Selective Context 进入模型前必须通过的全部质量与安全条件。"""

    mandatory_coverage_complete: bool
    authority_verified: bool
    revision_current: bool
    unknown_count: int = 0
    conflict_count: int = 0
    stale_count: int = 0
    role_scope_valid: bool = True
    e1_boundary_valid: bool = True
    path_policy_valid: bool = True
    secret_scan_valid: bool = True
    capability_valid: bool = True
    critical_false_omission: int = 0
    security_miss: int = 0
    privacy_miss: int = 0
    approved_scope_miss: int = 0

    def __post_init__(self) -> None:
        for name in (
            "mandatory_coverage_complete",
            "authority_verified",
            "revision_current",
            "role_scope_valid",
            "e1_boundary_valid",
            "path_policy_valid",
            "secret_scan_valid",
            "capability_valid",
        ):
            if not isinstance(getattr(self, name), bool):
                raise RuntimeValidationError("F14_SELECTIVE_GATE_INPUT_INVALID")
        for name in (
            "unknown_count",
            "conflict_count",
            "stale_count",
            "critical_false_omission",
            "security_miss",
            "privacy_miss",
            "approved_scope_miss",
        ):
            _non_negative(getattr(self, name), name)

    @classmethod
    def from_comparison(cls, comparison: Any, **overrides: Any) -> "SelectiveGateInput":
        values = {
            "mandatory_coverage_complete": bool(getattr(comparison, "mandatory_complete", False)),
            "authority_verified": not bool(getattr(comparison, "mandatory_unknown", ())),
            "revision_current": not bool(getattr(comparison, "mandatory_stale", ())),
            "unknown_count": len(getattr(comparison, "mandatory_unknown", ())),
            "conflict_count": len(getattr(comparison, "mandatory_conflict", ())),
            "stale_count": len(getattr(comparison, "mandatory_stale", ())),
        }
        values.update(overrides)
        return cls(**values)


@dataclass(frozen=True)
class ContextDeliveryDecision:
    """记录实际交付路径；F13 回退和 BLOCK 不会伪装成 Selective 成功。"""

    result: str
    delivery: str
    reasons: tuple[str, ...]
    savings: ContextSavings
    gate: SelectiveGateInput
    fallback_to_f13: bool
    input_hash: str

    def __post_init__(self) -> None:
        if self.result not in DECISION_RESULTS:
            raise RuntimeValidationError("F14_CONTEXT_DECISION_INVALID")
        if self.delivery not in {F13_FULL, F14_SELECTIVE, DELIVERY_BLOCKED}:
            raise RuntimeValidationError("F14_CONTEXT_DELIVERY_INVALID")

    @property
    def allowed(self) -> bool:
        return self.result != "BLOCKED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "delivery": self.delivery,
            "reasons": list(self.reasons),
            "context_savings": self.savings.to_dict(),
            "gate": {
                "mandatory_coverage_complete": self.gate.mandatory_coverage_complete,
                "authority_verified": self.gate.authority_verified,
                "revision_current": self.gate.revision_current,
                "unknown_count": self.gate.unknown_count,
                "conflict_count": self.gate.conflict_count,
                "stale_count": self.gate.stale_count,
                "role_scope_valid": self.gate.role_scope_valid,
                "e1_boundary_valid": self.gate.e1_boundary_valid,
                "path_policy_valid": self.gate.path_policy_valid,
                "secret_scan_valid": self.gate.secret_scan_valid,
                "capability_valid": self.gate.capability_valid,
            },
            "fallback_to_f13": self.fallback_to_f13,
            "input_hash": self.input_hash,
        }


class SelectiveContextGate:
    """把 Feature Flag、质量门禁和净节省统一为一个可审计决定。"""

    def evaluate(
        self,
        *,
        flags: F14FeatureFlags,
        role: str,
        phase: str,
        savings: ContextSavings,
        gate: SelectiveGateInput,
        project_id: str | None = None,
        benchmark: bool = False,
        explicit_canary: bool = False,
        test_project: bool = False,
        optimization_error: str | None = None,
    ) -> ContextDeliveryDecision:
        payload = {
            "flags": flags.to_dict(),
            "role": role,
            "phase": phase,
            "project_id": project_id,
            "benchmark": benchmark,
            "explicit_canary": explicit_canary,
            "test_project": test_project,
            "savings": savings.to_dict(),
            "gate": gate.__dict__,
        }
        input_hash = _hash(payload)
        if optimization_error:
            return ContextDeliveryDecision(
                "FALLBACK_F13", F13_FULL, ("optimization_failure", optimization_error),
                savings, gate, True, input_hash
            )
        if not flags.selective_allowed(
            role=role,
            phase=phase,
            project_id=project_id,
            benchmark=benchmark,
            explicit_canary=explicit_canary,
            test_project=test_project,
        ):
            return ContextDeliveryDecision(
                "FALLBACK_F13", F13_FULL, ("feature_flag_disabled_or_not_canary",),
                savings, gate, True, input_hash
            )
        blocking_reasons: list[str] = []
        if not gate.mandatory_coverage_complete:
            blocking_reasons.append("mandatory_coverage_incomplete")
        if not gate.authority_verified:
            blocking_reasons.append("authority_unverified")
        if not gate.revision_current:
            blocking_reasons.append("revision_stale")
        for name, reason in (
            (gate.unknown_count, "unknown_mandatory"),
            (gate.conflict_count, "mandatory_conflict"),
            (gate.stale_count, "stale_source"),
            (gate.critical_false_omission, "critical_false_omission"),
            (gate.security_miss, "security_miss"),
            (gate.privacy_miss, "privacy_miss"),
            (gate.approved_scope_miss, "approved_scope_miss"),
        ):
            if name:
                blocking_reasons.append(reason)
        for valid, reason in (
            (gate.role_scope_valid, "role_scope_invalid"),
            (gate.e1_boundary_valid, "e1_boundary_invalid"),
            (gate.path_policy_valid, "path_policy_invalid"),
            (gate.secret_scan_valid, "secret_scan_invalid"),
            (gate.capability_valid, "capability_invalid"),
        ):
            if not valid:
                blocking_reasons.append(reason)
        if blocking_reasons:
            return ContextDeliveryDecision(
                "BLOCKED", DELIVERY_BLOCKED, tuple(blocking_reasons), savings, gate, False, input_hash
            )
        if savings.net_reduction_ratio <= 0:
            return ContextDeliveryDecision(
                "FALLBACK_F13", F13_FULL, ("no_net_context_reduction",), savings, gate, True, input_hash
            )
        return ContextDeliveryDecision(
            F14_SELECTIVE, F14_SELECTIVE, (), savings, gate, False, input_hash
        )


class F14ContextDeliveryService:
    """在 F13 Package 上执行受控 Selective 派生，失败立即回退 F13。"""

    def __init__(self, flags: F14FeatureFlags | None = None) -> None:
        self.flags = flags or F14FeatureFlags.load()
        self.gate = SelectiveContextGate()

    def deliver(
        self,
        current: Any,
        *,
        role: str,
        phase: str,
        selected_references: Iterable[str],
        mandatory_references: Iterable[str] = (),
        gate: SelectiveGateInput | None = None,
        project_id: str | None = None,
        benchmark: bool = False,
        explicit_canary: bool = False,
        test_project: bool = False,
        expansion_bytes: int = 0,
        recovery_additional_bytes: int = 0,
        persist_store: Any | None = None,
    ) -> tuple[Any, ContextDeliveryDecision]:
        """返回 (实际交付 Package, 可审计 Decision)。"""

        try:
            source_map = {source.reference: source for source in current.sources}
            selected = tuple(sorted(set(selected_references) | set(mandatory_references)))
            initial_bytes = sum(
                source_map[reference].included_size
                for reference in selected
                if reference in source_map
            )
            savings = ContextSavings(
                int(current.inline_bytes),
                initial_bytes,
                expansion_bytes,
                recovery_additional_bytes,
            )
            resolved_gate = gate or SelectiveGateInput(
                mandatory_coverage_complete=all(
                    reference in source_map for reference in mandatory_references
                ),
                authority_verified=True,
                revision_current=True,
            )
            decision = self.gate.evaluate(
                flags=self.flags,
                role=role,
                phase=phase,
                project_id=project_id,
                benchmark=benchmark,
                explicit_canary=explicit_canary,
                test_project=test_project,
                savings=savings,
                gate=resolved_gate,
            )
            if decision.result != F14_SELECTIVE:
                return current, decision
            from runtime.context.selective import SelectiveContextBuilder

            delivered = SelectiveContextBuilder().build(
                current,
                selected_references=selected,
                mandatory_references=mandatory_references,
                persist_store=persist_store,
            )
            return delivered, decision
        except Exception as exc:
            fallback_savings = ContextSavings(
                int(getattr(current, "inline_bytes", 0)),
                int(getattr(current, "inline_bytes", 0)),
                0,
                0,
            )
            fallback_gate = gate or SelectiveGateInput(True, True, True)
            decision = self.gate.evaluate(
                flags=self.flags,
                role=role,
                phase=phase,
                project_id=project_id,
                benchmark=benchmark,
                explicit_canary=explicit_canary,
                test_project=test_project,
                savings=fallback_savings,
                gate=fallback_gate,
                optimization_error=type(exc).__name__,
            )
            return current, decision


@dataclass(frozen=True)
class CanaryPromotionMetrics:
    """每个 Role/Phase 扩大 Canary 前必须为零的质量风险。"""

    critical_false_omission: int = 0
    security_miss: int = 0
    privacy_miss: int = 0
    approved_scope_miss: int = 0
    false_block: int = 0
    e1_regression: int = 0
    evaluator_detection_difference: int = 0
    existing_regression: int = 0
    net_reduction_ratio: float = 0.0

    def can_promote(self) -> bool:
        return (
            all(
                value == 0
                for value in (
                    self.critical_false_omission,
                    self.security_miss,
                    self.privacy_miss,
                    self.approved_scope_miss,
                    self.false_block,
                    self.e1_regression,
                    self.evaluator_detection_difference,
                    self.existing_regression,
                )
            )
            and self.net_reduction_ratio > 0
        )

    def reasons(self) -> tuple[str, ...]:
        names = (
            "critical_false_omission",
            "security_miss",
            "privacy_miss",
            "approved_scope_miss",
            "false_block",
            "e1_regression",
            "evaluator_detection_difference",
            "existing_regression",
        )
        return tuple(name for name in names if getattr(self, name) != 0) + (
            ("no_net_reduction",) if self.net_reduction_ratio <= 0 else ()
        )


__all__ = [
    "BASELINE_IDS",
    "DELIVERY_BLOCKED",
    "DELIVERY_MODES",
    "DECISION_RESULTS",
    "F13_FULL",
    "F14_SELECTIVE",
    "CanaryPromotionMetrics",
    "ContextDeliveryDecision",
    "ContextSavings",
    "F14BaselineFreeze",
    "F14FeatureFlags",
    "F14RolloutPolicy",
    "F14ContextDeliveryService",
    "SelectiveContextGate",
    "SelectiveGateInput",
    "compute_context_savings",
]
