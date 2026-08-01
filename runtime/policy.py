"""由配置驱动的 Runtime 提交权限校验。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.project_state import parse_project_yaml

from .errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[1]


def load_field_ownership() -> dict[str, frozenset[str]]:
    """读取唯一的角色字段归属配置，配置损坏即阻止启动。"""

    document = parse_project_yaml(
        (_ROOT / "config" / "role_policies.yaml").read_text(encoding="utf-8")
    )
    raw = document.get("field_ownership")
    if not isinstance(raw, dict):
        raise RuntimeValidationError("ROLE_POLICY_INVALID")
    result: dict[str, frozenset[str]] = {}
    for actor, fields in raw.items():
        if not isinstance(actor, str) or not isinstance(fields, list) or not all(
            isinstance(item, str) for item in fields
        ):
            raise RuntimeValidationError("ROLE_POLICY_INVALID")
        result[actor] = frozenset(fields)
    return result


def load_runtime_routes() -> dict[str, dict[str, Any]]:
    """读取唯一工作流配置中的状态路由，配置损坏时拒绝启动。"""

    document = parse_project_yaml(
        (_ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
    )
    states = document.get("states")
    if not isinstance(states, dict):
        raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
    routes: dict[str, dict[str, Any]] = {}
    for status, route in states.items():
        if not isinstance(status, str) or not isinstance(route, dict):
            raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
        next_role = route.get("next_role")
        active_module = route.get("active_module")
        if next_role is not None and not isinstance(next_role, str):
            raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
        if active_module is not None and not isinstance(active_module, str):
            raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
        routes[status] = {
            "next_role": next_role,
            "active_module": active_module,
            "wait_for_user": bool(route.get("wait_for_user", False)),
        }
    return routes


def assert_state_transition(source_status: str, target_status: str) -> None:
    """按 workflow.yaml 的唯一迁移表拒绝非法状态变化。"""

    document = parse_project_yaml(
        (_ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
    )
    transitions = document.get("state_transitions")
    if not isinstance(transitions, dict):
        raise RuntimeValidationError("WORKFLOW_TRANSITION_CONFIG_INVALID")
    allowed = transitions.get(source_status)
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise RuntimeValidationError("WORKFLOW_TRANSITION_CONFIG_INVALID")
    if source_status == target_status:
        return
    if target_status not in allowed:
        raise RuntimeValidationError(
            f"ILLEGAL_STATE_TRANSITION:{source_status}->{target_status}"
        )


def changed_top_level_fields(before: dict[str, Any], after: dict[str, Any]) -> frozenset[str]:
    """计算完整候选状态的显式顶层 diff。"""

    return frozenset(
        field
        for field in set(before) | set(after)
        if before.get(field) != after.get(field)
    )


def assert_field_ownership(
    actor: str, before: dict[str, Any], after: dict[str, Any]
) -> None:
    """拒绝角色越权、隐藏字段变更以及 Runtime 业务字段变更。"""

    allowed = load_field_ownership().get(actor)
    if allowed is None:
        raise RuntimeValidationError("ROLE_POLICY_UNKNOWN_ACTOR")
    changed = changed_top_level_fields(before, after)
    forbidden = changed - allowed
    if forbidden:
        raise RuntimeValidationError(
            "ROLE_FIELD_OWNERSHIP_VIOLATION:" + ",".join(sorted(forbidden))
        )
