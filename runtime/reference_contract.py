"""Generator 使用的 Approved Reference Contract 运行时适配层。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.project_state import ProjectStateError, load_project_state
from scripts.reference_contract import (
    build_approved_reference_contract,
    canonical_reference_contract,
    reference_contract_context_hash,
    validate_approved_reference_contract,
)


def _read_text(
    root: Path,
    reference: object,
    *,
    path_policy: ExecutionPathPolicy,
    actor: str,
) -> str:
    if not isinstance(reference, str) or not reference.strip():
        raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_REFERENCE_MISSING")
    path = path_policy.assert_path(actor, root, reference, operation="read")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectStateError("REFERENCE_CONTRACT_SOURCE_READ_FAILED:" + reference) from exc


def _read_synthesis(
    root: Path,
    reference: object,
    *,
    path_policy: ExecutionPathPolicy,
) -> dict[str, Any]:
    if not isinstance(reference, str) or not reference.strip():
        raise ProjectStateError("REFERENCE_CONTRACT_SYNTHESIS_REFERENCE_MISSING")
    # Reference Synthesis 属于 Reference Analysis Module 的受控输入；Generator 只能
    # 通过本适配层取得已批准决策，不直接接触原始 Reference 内容。
    path = path_policy.assert_module_path(
        "reference_analysis", root, reference, operation="read"
    )
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectStateError("REFERENCE_CONTRACT_SYNTHESIS_READ_FAILED") from exc
    if not isinstance(value, dict):
        raise ProjectStateError("REFERENCE_CONTRACT_SYNTHESIS_INVALID")
    return value


def build_reference_contract_for_project(
    project_root: str | Path,
    *,
    path_policy: ExecutionPathPolicy | None = None,
) -> dict[str, Any] | None:
    """从当前项目批准链确定性生成契约；未采用 REFDEC 时返回 ``None``。"""

    root = Path(project_root).resolve()
    policy = path_policy or ExecutionPathPolicy()
    state = load_project_state(root / "project.yaml")
    synthesis_ref = state.get("active_reference_synthesis")
    if synthesis_ref in (None, ""):
        return None
    synthesis = _read_synthesis(root, synthesis_ref, path_policy=policy)
    product_spec_ref = state.get("active_product_spec")
    approved_plan_ref = state.get("approved_plan")
    plan_approval_ref = state.get("plan_approval_record")
    if not isinstance(product_spec_ref, str) or not isinstance(approved_plan_ref, str):
        raise ProjectStateError("REFERENCE_CONTRACT_APPROVED_SOURCE_INVALID")
    if not isinstance(plan_approval_ref, str) or not plan_approval_ref.strip():
        raise ProjectStateError("REFERENCE_CONTRACT_PLAN_APPROVAL_MISSING")
    product_spec_text = _read_text(
        root, product_spec_ref, path_policy=policy, actor="generator"
    )
    approved_plan_text = _read_text(
        root, approved_plan_ref, path_policy=policy, actor="generator"
    )
    requirements: dict[str, Any] | None = None
    requirements_ref = state.get("active_requirements")
    if requirements_ref not in (None, ""):
        raw_requirements = _read_text(
            root, requirements_ref, path_policy=policy, actor="generator"
        )
        try:
            parsed_requirements = yaml.safe_load(raw_requirements)
        except yaml.YAMLError as exc:
            raise ProjectStateError("REFERENCE_CONTRACT_REQUIREMENTS_INVALID") from exc
        if parsed_requirements is not None and not isinstance(parsed_requirements, dict):
            raise ProjectStateError("REFERENCE_CONTRACT_REQUIREMENTS_INVALID")
        requirements = parsed_requirements
    contract = build_approved_reference_contract(
        synthesis,
        synthesis_ref=synthesis_ref,
        active_requirements_ref=requirements_ref if isinstance(requirements_ref, str) else None,
        product_spec_ref=product_spec_ref,
        approved_plan_ref=approved_plan_ref,
        product_approval_ref=(
            state.get("product_approval_record")
            if isinstance(state.get("product_approval_record"), str)
            else None
        ),
        plan_approval_ref=plan_approval_ref,
        product_spec_text=product_spec_text,
        approved_plan_text=approved_plan_text,
        requirements=requirements,
        state=state,
    )
    if contract is not None:
        validate_approved_reference_contract(contract)
    return contract


__all__ = [
    "build_reference_contract_for_project",
    "canonical_reference_contract",
    "reference_contract_context_hash",
    "validate_approved_reference_contract",
]
