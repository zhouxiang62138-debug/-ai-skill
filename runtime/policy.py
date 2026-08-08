"""由配置驱动的 Runtime 提交权限校验。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from scripts.project_state import parse_project_yaml

from .errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[1]

CAPABILITY_ALLOW = "ALLOW"
CAPABILITY_DENY = "DENY"

_CAPABILITY_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|token|secret|password|private[_-]?key)\s*[:=]"
    r"|(?:bearer\s+|ghp_|github_pat_|sk-[A-Za-z0-9])"
)


def _capability_config_path(config_path: str | Path | None = None) -> Path:
    return Path(config_path) if config_path else _ROOT / "config" / "role_policies.yaml"


def _load_capability_config(
    config_path: str | Path | None = None,
) -> tuple[frozenset[str], dict[str, frozenset[str]]]:
    """读取与路径策略相同的角色配置，不从代码生成权限列表。"""

    document = parse_project_yaml(_capability_config_path(config_path).read_text(encoding="utf-8"))
    raw_capabilities = document.get("capabilities")
    roles = document.get("roles")
    if (
        not isinstance(raw_capabilities, list)
        or not raw_capabilities
        or not all(isinstance(item, str) and item for item in raw_capabilities)
        or not isinstance(roles, dict)
    ):
        raise RuntimeValidationError("CAPABILITY_POLICY_INVALID")

    capabilities = frozenset(raw_capabilities)
    role_capabilities: dict[str, frozenset[str]] = {}
    for role, policy in roles.items():
        if not isinstance(role, str) or not isinstance(policy, dict):
            raise RuntimeValidationError("CAPABILITY_POLICY_INVALID")
        raw_role_capabilities = policy.get("capabilities")
        if (
            not isinstance(raw_role_capabilities, list)
            or not all(isinstance(item, str) and item for item in raw_role_capabilities)
            or not set(raw_role_capabilities).issubset(capabilities)
        ):
            raise RuntimeValidationError("CAPABILITY_POLICY_INVALID")
        role_capabilities[role] = frozenset(raw_role_capabilities)
    return capabilities, role_capabilities


def load_capabilities(config_path: str | Path | None = None) -> frozenset[str]:
    """返回配置声明的全部 Capability 名称。"""

    return _load_capability_config(config_path)[0]


def load_role_capabilities(
    config_path: str | Path | None = None,
) -> dict[str, frozenset[str]]:
    """返回配置声明的 Role Capability 映射。"""

    return _load_capability_config(config_path)[1]


def _validate_capability_context(resource: str | None, action: str | None) -> None:
    for name, value in (("resource", resource), ("action", action)):
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise RuntimeValidationError("CAPABILITY_REQUEST_INVALID")
        if isinstance(value, str) and _CAPABILITY_SECRET.search(value):
            raise RuntimeValidationError("CAPABILITY_SECRET_FORBIDDEN")


class CapabilityPolicy:
    """由 role_policies.yaml 驱动的确定性 Capability Gate。"""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._capabilities, self._role_capabilities = _load_capability_config(config_path)

    @property
    def capabilities(self) -> frozenset[str]:
        return self._capabilities

    @property
    def role_capabilities(self) -> dict[str, frozenset[str]]:
        return dict(self._role_capabilities)

    def authorize(
        self,
        role: str,
        capability: str,
        resource: str | None = None,
        action: str | None = None,
    ) -> str:
        """允许返回 ALLOW；其余情况以稳定错误拒绝，默认拒绝。"""

        if not isinstance(role, str) or role not in self._role_capabilities:
            raise RuntimeValidationError("UNKNOWN_ROLE")
        if not isinstance(capability, str) or capability not in self._capabilities:
            raise RuntimeValidationError("UNKNOWN_CAPABILITY")
        _validate_capability_context(resource, action)
        if capability not in self._role_capabilities[role]:
            raise RuntimeValidationError("CAPABILITY_DENIED")
        return CAPABILITY_ALLOW

    def check(
        self,
        role: str,
        capability: str,
        resource: str | None = None,
        action: str | None = None,
    ) -> str:
        """返回 ALLOW 或 DENY；需要错误原因时使用 authorize。"""

        try:
            return self.authorize(role, capability, resource, action)
        except RuntimeValidationError:
            return CAPABILITY_DENY


def authorize(
    role: str,
    capability: str,
    resource: str | None = None,
    action: str | None = None,
) -> str:
    """使用正式配置执行一次 Capability 授权检查。"""

    return CapabilityPolicy().authorize(role, capability, resource, action)


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


def load_lifecycle_fields() -> frozenset[str]:
    """读取 Runtime CAS 独占提交的工作流生命周期字段。"""

    document = parse_project_yaml(
        (_ROOT / "config" / "role_policies.yaml").read_text(encoding="utf-8")
    )
    authority = document.get("lifecycle_authority")
    if not isinstance(authority, dict) or authority.get("owner") != "runtime_cas":
        raise RuntimeValidationError("LIFECYCLE_AUTHORITY_POLICY_INVALID")
    raw_fields = authority.get("fields")
    if (
        not isinstance(raw_fields, list)
        or not raw_fields
        or not all(isinstance(item, str) and item for item in raw_fields)
    ):
        raise RuntimeValidationError("LIFECYCLE_AUTHORITY_POLICY_INVALID")
    fields = frozenset(raw_fields)
    if fields != frozenset(
        {"status", "next_role", "active_module", "active_change_request"}
    ):
        raise RuntimeValidationError("LIFECYCLE_AUTHORITY_POLICY_INVALID")
    return fields


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
        transition_actor = route.get("transition_actor")
        if next_role is not None and not isinstance(next_role, str):
            raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
        if active_module is not None and not isinstance(active_module, str):
            raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
        if transition_actor is not None and not isinstance(transition_actor, str):
            raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")
        routes[status] = {
            "next_role": next_role,
            "active_module": active_module,
            "transition_actor": transition_actor,
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


def assert_workflow_lifecycle(
    source_status: str,
    target_status: str,
    actor: str,
    changed_fields: dict[str, Any],
) -> None:
    """验证角色步骤，再由内部 Runtime CAS 提交生命周期投影。"""

    if not isinstance(changed_fields, dict):
        raise RuntimeValidationError("WORKFLOW_LIFECYCLE_PATCH_INVALID")
    assert_state_transition(source_status, target_status)
    routes = load_runtime_routes()
    source_route = routes.get(source_status)
    target_route = routes.get(target_status)
    if source_route is None or target_route is None:
        raise RuntimeValidationError("WORKFLOW_ROUTE_CONFIG_INVALID")

    expected_actor = (
        source_route["transition_actor"]
        or source_route["active_module"]
        or source_route["next_role"]
    )
    if actor != expected_actor:
        raise RuntimeValidationError("WORKFLOW_ACTOR_MISMATCH")

    lifecycle_fields = load_lifecycle_fields()
    if source_status != target_status:
        route_fields = {
            field
            for field in lifecycle_fields
            if field in source_route or field in target_route
        }
        route_fields.add("status")
        missing = route_fields - set(changed_fields)
        if missing:
            raise RuntimeValidationError(
                "WORKFLOW_LIFECYCLE_FIELDS_MISSING:" + ",".join(sorted(missing))
            )
    for field in lifecycle_fields - {"status", "active_change_request"}:
        if field in changed_fields and changed_fields[field] != target_route[field]:
            raise RuntimeValidationError("WORKFLOW_ROUTE_MISMATCH:" + field)


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
