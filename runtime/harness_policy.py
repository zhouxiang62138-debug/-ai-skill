"""按模型能力和任务风险选择 Harness 深度；未知情况默认 FULL。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import ProjectStateError, parse_project_yaml

from .context.rollover import RolloverPolicy
from .errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[1]
_MODES = frozenset({"LEAN", "STANDARD", "FULL"})
_RISK_TAGS = frozenset(
    {
        "database_migration",
        "authentication",
        "authorization",
        "payment",
        "destructive_operation",
        "external_api_integration",
        "complex_state_machine",
        "data_compatibility",
        "high_risk_change_request",
        "irreversible",
    }
)


@dataclass(frozen=True)
class HarnessDecision:
    mode: str
    reason_codes: tuple[str, ...]
    required_gates: tuple[str, ...]
    rollover_policy: RolloverPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason_codes": list(self.reason_codes),
            "required_gates": list(self.required_gates),
            "rollover_policy": {
                "enabled": self.rollover_policy.enabled,
                "context_budget_percent": self.rollover_policy.context_budget_percent,
                "max_tool_calls": self.rollover_policy.max_tool_calls,
                "max_compactions": self.rollover_policy.max_compactions,
                "max_elapsed_seconds": self.rollover_policy.max_elapsed_seconds,
            },
        }


class HarnessPolicy:
    """Runtime 可调用的 Harness Policy；不是 Prompt 约定。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        path = Path(config_path or _ROOT / "config" / "harness_policy.yaml")
        try:
            document = parse_project_yaml(path.read_text(encoding="utf-8"))
        except (OSError, ProjectStateError) as exc:
            raise RuntimeValidationError("HARNESS_POLICY_UNAVAILABLE") from exc
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise RuntimeValidationError("HARNESS_POLICY_INVALID")
        default_mode = document.get("default_mode")
        if default_mode not in _MODES:
            raise RuntimeValidationError("HARNESS_POLICY_INVALID")
        known_models = document.get("known_models")
        if not isinstance(known_models, list):
            raise RuntimeValidationError("HARNESS_POLICY_INVALID")
        self._known_models = {
            item.get("model_id"): item.get("capability_profile")
            for item in known_models
            if isinstance(item, Mapping)
            and isinstance(item.get("model_id"), str)
            and isinstance(item.get("capability_profile"), str)
        }
        self._default_mode = str(default_mode)
        self._mandatory_gates = tuple(document.get("mandatory_gates", ()))
        limits = document.get("mode_limits")
        if not isinstance(limits, Mapping):
            raise RuntimeValidationError("HARNESS_POLICY_INVALID")
        self._limits: dict[str, dict[str, Any]] = {}
        for mode in _MODES:
            raw = limits.get(mode)
            if not isinstance(raw, Mapping):
                raise RuntimeValidationError("HARNESS_POLICY_INVALID")
            self._limits[mode] = dict(raw)

    @property
    def known_models(self) -> frozenset[str]:
        return frozenset(self._known_models)

    def choose(
        self,
        *,
        model_id: str,
        model_capability_profile: str | None = None,
        task_complexity: str = "medium",
        risk_tags: tuple[str, ...] = (),
        acceptance_criteria_count: int = 0,
        key_workflow_count: int = 0,
        browser_required: bool = False,
        irreversible: bool = False,
        historical_pass_rate: float | None = None,
        historical_return_count: int = 0,
        evidence_sufficient: bool = True,
    ) -> HarnessDecision:
        reasons: list[str] = []
        mode = self._default_mode
        known_profile = self._known_models.get(model_id)
        if known_profile is None or (
            model_capability_profile is not None
            and model_capability_profile != known_profile
        ):
            reasons.append("unknown_model_or_capability")
            mode = "FULL"
        elif not evidence_sufficient:
            reasons.append("evidence_insufficient")
            mode = "FULL"
        else:
            normalized_risks = set(risk_tags)
            if normalized_risks - _RISK_TAGS:
                reasons.append("unknown_risk")
                mode = "FULL"
            elif irreversible or normalized_risks & _RISK_TAGS or browser_required:
                reasons.append("high_risk_or_browser_required")
                mode = "FULL"
            elif task_complexity not in {"low", "medium", "high"}:
                reasons.append("unknown_task_complexity")
                mode = "FULL"
            elif task_complexity == "low" and acceptance_criteria_count <= 3 and key_workflow_count <= 1:
                reasons.append("low_task_complexity")
                mode = "LEAN"
            else:
                reasons.append("standard_task_complexity")
                mode = "STANDARD"
            if historical_pass_rate is not None and historical_pass_rate < 0.9:
                reasons.append("historical_pass_rate_low")
                mode = "FULL"
            if historical_return_count > 1:
                reasons.append("historical_rework_high")
                mode = "FULL"
        limits = self._limits[mode]
        rollover = RolloverPolicy(
            enabled=True,
            context_budget_percent=int(limits["context_budget_percent"]),
            max_tool_calls=int(limits["max_tool_calls"]),
            max_compactions=int(limits["max_compactions"]),
            max_elapsed_seconds=int(limits["max_elapsed_seconds"]),
        )
        gates = list(self._mandatory_gates)
        if browser_required and "browser_acceptance" not in gates:
            gates.append("browser_acceptance")
        return HarnessDecision(mode, tuple(dict.fromkeys(reasons)), tuple(gates), rollover)


def choose_harness_policy(**kwargs: Any) -> HarnessDecision:
    """便捷入口；任何未知模型/风险都会走 FULL。"""

    return HarnessPolicy().choose(**kwargs)


__all__ = ["HarnessDecision", "HarnessPolicy", "choose_harness_policy"]
