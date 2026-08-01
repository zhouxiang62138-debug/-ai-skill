"""根据业务状态确定性选择现有角色、Module 或等待。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import RuntimeValidationError
from .policy import load_runtime_routes


@dataclass(frozen=True)
class Selection:
    kind: str
    target: str | None
    reason: str


def select_role(state: dict[str, Any]) -> Selection:
    """只根据受验证字段选择目标，不进行产品或验收决策。"""

    status = state.get("status")
    module = state.get("active_module")
    next_role = state.get("next_role")
    routes = load_runtime_routes()
    route = routes.get(str(status))
    if route is None:
        raise RuntimeValidationError(f"状态没有配置 Runtime 路由：{status}")
    if route["wait_for_user"] or (
        route["next_role"] is None and route["active_module"] is None
    ):
        return Selection("WAIT", None, f"status:{status}")
    if route["active_module"] is not None:
        if module != route["active_module"]:
            raise RuntimeValidationError(
                f"状态 {status} 要求 active_module={route['active_module']}，实际为 {module}"
            )
        if next_role is not None:
            raise RuntimeValidationError("First-Ask Module 不得同时声明 next_role")
        return Selection("MODULE", str(route["active_module"]), "configured_active_module")
    expected = route["next_role"]
    if next_role != expected:
        raise RuntimeValidationError(
            f"状态 {status} 要求 next_role={expected}，实际为 {next_role}"
        )
    return Selection("ROLE", expected, f"status:{status}")
