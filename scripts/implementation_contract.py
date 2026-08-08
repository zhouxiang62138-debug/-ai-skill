"""Stage 5 高风险任务的确定性 Implementation Contract。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from project_state import ProjectStateError, parse_project_yaml, serialize_project_state


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "implementation_contract.yaml"
CONTRACT_DIRECTORY = Path("memory") / "handoffs"
CONTRACT_PATTERN = re.compile(r"^implementation-contract-(\d{3})\.yaml$")
CONTRACT_ID_PATTERN = re.compile(r"^implementation-contract-(\d{3})$")
VERIFICATION_TYPES = {"unit", "integration", "browser", "persistence", "regression"}
DEFAULT_TAGS = {
    "database_migration",
    "authentication",
    "authorization",
    "payment",
    "destructive_operation",
    "external_api_integration",
    "complex_state_machine",
    "data_compatibility",
    "high_risk_change_request",
}


@dataclass(frozen=True)
class ContractDecision:
    required: bool
    triggers: tuple[str, ...]
    reason: str


def _error(message: str) -> ProjectStateError:
    return ProjectStateError(f"Implementation Contract 无效：{message}")


def _nonempty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{label} 必须是非空字符串")


def _string_list(value: Any, label: str, *, required: bool = True) -> None:
    if not isinstance(value, list) or (required and not value):
        raise _error(f"{label} 必须是{'' if required else '可为空的'}字符串列表")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise _error(f"{label} 只能包含非空字符串")


def _relative_reference(value: Any, label: str) -> None:
    _nonempty_string(value, label)
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or "../" in f"{normalized}/":
        raise _error(f"{label} 必须是项目内相对路径")


def _contract_files(project_root: Path) -> list[Path]:
    directory = project_root / CONTRACT_DIRECTORY
    if not directory.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for path in directory.iterdir():
        match = CONTRACT_PATTERN.fullmatch(path.name)
        if match and path.is_file():
            found.append((int(match.group(1)), path))
    return [path for _, path in sorted(found)]


def _load_policy(policy_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(policy_path or DEFAULT_POLICY_PATH)
    try:
        value = parse_project_yaml(path.read_text(encoding="utf-8"))
    except (OSError, ProjectStateError) as exc:
        raise _error("无法读取 Contract 策略") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise _error("Contract 策略 schema_version 必须为 1")
    return value


def _policy_int(policy: Mapping[str, Any], key: str, default: int) -> int:
    thresholds = policy.get("thresholds", {})
    value = thresholds.get(key, default) if isinstance(thresholds, dict) else default
    if not isinstance(value, int) or value < 1:
        raise _error(f"thresholds.{key} 必须是正整数")
    return value


def classify_contract_need(
    feature: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> ContractDecision:
    """根据结构化特征判断是否进入 Contract，不解析自然语言猜测风险。"""

    current_policy = policy or _load_policy()
    configured_tags = current_policy.get("trigger_tags", sorted(DEFAULT_TAGS))
    if not isinstance(configured_tags, list) or any(not isinstance(tag, str) for tag in configured_tags):
        raise _error("trigger_tags 必须是字符串列表")
    tags = feature.get("risk_tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise _error("risk_tags 必须是字符串列表")
    allowed = set(configured_tags) | DEFAULT_TAGS
    triggers: list[str] = [tag for tag in dict.fromkeys(tags) if tag in allowed]
    if feature.get("irreversible") is True:
        triggers.append("irreversible")
    critical_pages = feature.get("critical_workflow_pages", 0)
    page_threshold = _policy_int(current_policy, "critical_workflow_pages", 2)
    if not isinstance(critical_pages, int) or critical_pages < 0:
        raise _error("critical_workflow_pages 必须是非负整数")
    if critical_pages >= page_threshold:
        triggers.append("critical_workflow_pages")
    ac_count = feature.get("acceptance_criteria_count", 0)
    ac_threshold = _policy_int(current_policy, "acceptance_criteria_count", 5)
    if not isinstance(ac_count, int) or ac_count < 0:
        raise _error("acceptance_criteria_count 必须是非负整数")
    if ac_count >= ac_threshold:
        triggers.append("many_acceptance_criteria")
    unique_triggers = tuple(dict.fromkeys(triggers))
    if not unique_triggers:
        return ContractDecision(False, (), "未命中高风险触发条件")
    return ContractDecision(True, unique_triggers, "命中高风险触发条件")


def _required_verification(
    triggers: tuple[str, ...], policy: Mapping[str, Any]
) -> set[str]:
    mapping = policy.get("verification_by_trigger", {})
    if not isinstance(mapping, dict):
        raise _error("verification_by_trigger 必须是对象")
    required: set[str] = set()
    for trigger in triggers:
        values = mapping.get(trigger, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise _error(f"verification_by_trigger.{trigger} 必须是字符串列表")
        required.update(values)
    invalid = required - VERIFICATION_TYPES
    if invalid:
        raise _error(f"策略包含未知验证类型：{sorted(invalid)}")
    return required


def validate_contract(
    contract: Mapping[str, Any],
    *,
    approved_plan: str | None = None,
    approved_requirements: set[str] | None = None,
    approved_acceptance_criteria: set[str] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> None:
    """校验 Contract，确保它不能扩展批准来源，也不能变成 Approval Gate。"""

    if not isinstance(contract, Mapping):
        raise _error("Contract 必须是对象")
    required_fields = {
        "schema_version", "contract_id", "feature_id", "source_plan", "requirements",
        "acceptance_criteria", "risk_triggers", "done_when", "verification",
        "rollback_expectation", "risks",
    }
    if set(contract) != required_fields:
        raise _error("Contract 字段不完整或包含未声明字段")
    if contract["schema_version"] != 1:
        raise _error("schema_version 必须为 1")
    _nonempty_string(contract["contract_id"], "contract_id")
    if not CONTRACT_ID_PATTERN.fullmatch(contract["contract_id"]):
        raise _error("contract_id 格式无效")
    _nonempty_string(contract["feature_id"], "feature_id")
    _relative_reference(contract["source_plan"], "source_plan")
    if approved_plan is not None and contract["source_plan"] != approved_plan:
        raise _error("source_plan 与 approved_plan 不一致")
    _string_list(contract["requirements"], "requirements")
    _string_list(contract["acceptance_criteria"], "acceptance_criteria")
    if approved_requirements is not None and not set(contract["requirements"]) <= approved_requirements:
        raise _error("Contract 新增了未获批 Requirement")
    if approved_acceptance_criteria is not None and not set(contract["acceptance_criteria"]) <= approved_acceptance_criteria:
        raise _error("Contract 新增了未获批 Acceptance Criterion")
    _string_list(contract["risk_triggers"], "risk_triggers")
    _string_list(contract["done_when"], "done_when")
    _string_list(contract["verification"], "verification")
    invalid = set(contract["verification"]) - VERIFICATION_TYPES
    if invalid:
        raise _error(f"verification 包含未知类型：{sorted(invalid)}")
    _nonempty_string(contract["rollback_expectation"], "rollback_expectation")
    _string_list(contract["risks"], "risks", required=False)
    if "approval" in contract or "user_approval" in contract or "next_role" in contract:
        raise _error("Contract 不能成为新的用户批准门或生命周期状态")
    if policy is not None:
        required_verification = _required_verification(tuple(contract["risk_triggers"]), policy)
        if not required_verification <= set(contract["verification"]):
            raise _error("Contract 缺少由风险触发器要求的验证类型")


def create_implementation_contract(
    project_root: str | Path,
    *,
    feature: Mapping[str, Any],
    source_plan: str,
    requirements: list[str],
    acceptance_criteria: list[str],
    done_when: list[str],
    verification: list[str],
    rollback_expectation: str,
    risks: list[str],
    approved_plan: str | None = None,
    approved_requirements: set[str] | None = None,
    approved_acceptance_criteria: set[str] | None = None,
    policy_path: str | Path | None = None,
) -> Path | None:
    """仅在确定性风险判断为 required 时追加 Contract。"""

    policy = _load_policy(policy_path)
    decision = classify_contract_need(feature, policy=policy)
    if not decision.required:
        return None
    root = Path(project_root).resolve()
    paths = _contract_files(root)
    if paths:
        validate_contract_history(
            root,
            approved_plan=approved_plan,
            approved_requirements=approved_requirements,
            approved_acceptance_criteria=approved_acceptance_criteria,
            policy_path=policy_path,
        )
    number = len(paths) + 1
    contract = {
        "schema_version": 1,
        "contract_id": f"implementation-contract-{number:03d}",
        "feature_id": feature.get("feature_id"),
        "source_plan": source_plan,
        "requirements": requirements,
        "acceptance_criteria": acceptance_criteria,
        "risk_triggers": list(decision.triggers),
        "done_when": done_when,
        "verification": verification,
        "rollback_expectation": rollback_expectation,
        "risks": risks,
    }
    validate_contract(
        contract,
        approved_plan=approved_plan,
        approved_requirements=approved_requirements,
        approved_acceptance_criteria=approved_acceptance_criteria,
        policy=policy,
    )
    directory = root / CONTRACT_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"implementation-contract-{number:03d}.yaml"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise _error(f"拒绝覆盖已有 Contract：{target.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialize_project_state(contract).encode("utf-8"))
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return target


def validate_contract_history(
    project_root: str | Path,
    *,
    approved_plan: str | None = None,
    approved_requirements: set[str] | None = None,
    approved_acceptance_criteria: set[str] | None = None,
    policy_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """校验 Contract 历史编号连续且全部引用批准来源。"""

    root = Path(project_root).resolve()
    policy = _load_policy(policy_path)
    records: list[dict[str, Any]] = []
    for index, path in enumerate(_contract_files(root), 1):
        try:
            record = parse_project_yaml(path.read_text(encoding="utf-8"))
        except (OSError, ProjectStateError) as exc:
            raise _error(f"无法读取 {path.name}") from exc
        if not isinstance(record, dict):
            raise _error(f"{path.name} 顶层必须是对象")
        validate_contract(
            record,
            approved_plan=approved_plan,
            approved_requirements=approved_requirements,
            approved_acceptance_criteria=approved_acceptance_criteria,
            policy=policy,
        )
        expected_id = f"implementation-contract-{index:03d}"
        if record["contract_id"] != expected_id or path.name != f"{expected_id}.yaml":
            raise _error("Contract 编号或文件名不连续")
        records.append(record)
    return records


__all__ = [
    "ContractDecision",
    "classify_contract_need",
    "create_implementation_contract",
    "validate_contract",
    "validate_contract_history",
]
