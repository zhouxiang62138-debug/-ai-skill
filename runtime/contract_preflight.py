"""Runtime 强制执行的 Implementation Contract Preflight。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.implementation_contract import (
    ContractDecision,
    classify_contract_need,
    validate_contract_history,
)


@dataclass(frozen=True)
class ContractPreflightResult:
    """Contract 检查结果；失败只返回原因，不替模型扩大范围。"""

    required: bool
    passed: bool
    triggers: tuple[str, ...]
    contract_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "passed": self.passed,
            "triggers": list(self.triggers),
            "contract_ids": list(self.contract_ids),
            "errors": list(self.errors),
        }


def run_contract_preflight(
    project_root: str | Path,
    *,
    feature: Mapping[str, Any],
    approved_plan: str,
    approved_requirements: set[str],
    approved_acceptance_criteria: set[str],
    policy_path: str | Path | None = None,
) -> ContractPreflightResult:
    """在 Generator 调用模型前确定性检查高风险 Contract。"""

    if not isinstance(feature, Mapping):
        return ContractPreflightResult(
            required=True,
            passed=False,
            triggers=("risk_undetermined",),
            contract_ids=(),
            errors=("无法从受保护 Feature Manifest 确定风险，已 fail closed",),
        )
    decision: ContractDecision = classify_contract_need(feature, policy_path=policy_path)
    root = Path(project_root).resolve()
    try:
        records = validate_contract_history(
            root,
            approved_plan=approved_plan,
            approved_requirements=approved_requirements,
            approved_acceptance_criteria=approved_acceptance_criteria,
            policy_path=policy_path,
        )
    except Exception as exc:
        return ContractPreflightResult(
            required=decision.required,
            passed=False,
            triggers=decision.triggers,
            contract_ids=(),
            errors=(str(exc),),
        )
    if decision.required and not records:
        return ContractPreflightResult(
            required=True,
            passed=False,
            triggers=decision.triggers,
            contract_ids=(),
            errors=("高风险任务缺少 Implementation Contract",),
        )
    return ContractPreflightResult(
        required=decision.required,
        passed=True,
        triggers=decision.triggers,
        contract_ids=tuple(str(item["contract_id"]) for item in records),
        errors=(),
    )


__all__ = ["ContractPreflightResult", "run_contract_preflight"]
