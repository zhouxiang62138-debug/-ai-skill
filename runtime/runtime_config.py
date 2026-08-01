"""正式 Runtime 的集中配置读取。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.project_state import parse_project_yaml

from .errors import RuntimeValidationError


_ROOT = Path(__file__).resolve().parents[1]


def load_runtime_config() -> dict[str, Any]:
    """读取并校验 config/runtime.yaml，拒绝悄悄使用硬编码回退。"""

    document = parse_project_yaml(
        (_ROOT / "config" / "runtime.yaml").read_text(encoding="utf-8")
    )
    database = document.get("database")
    events = document.get("events")
    if not isinstance(database, dict) or not isinstance(events, dict):
        raise RuntimeValidationError("RUNTIME_CONFIG_INVALID")
    root = database.get("control_plane_root")
    busy_timeout = database.get("busy_timeout_ms")
    payload_limit = events.get("payload_limit_bytes")
    if (
        not isinstance(root, str) or not root
        or not isinstance(busy_timeout, int) or busy_timeout <= 0
        or not isinstance(payload_limit, int) or payload_limit <= 0
    ):
        raise RuntimeValidationError("RUNTIME_CONFIG_INVALID")
    return {
        "control_plane_root": root,
        "busy_timeout_ms": busy_timeout,
        "payload_limit_bytes": payload_limit,
    }
