"""F10 Session Control Plane 的位置与绑定规则。

控制平面绝不位于项目目录；项目只保存不可写的绑定标识。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .errors import RuntimeStorageError
from .runtime_config import load_runtime_config
from .session_store import SessionStore, utc_now


def control_plane_id(project_id: str) -> str:
    """由项目标识产生稳定、非敏感的控制平面标识。"""

    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
    return f"runtime-{digest}"


def control_plane_root(
    project_id: str, *, home: str | Path | None = None
) -> Path:
    """返回用户目录下的控制平面路径，不创建任何文件。"""

    configured_root = Path(load_runtime_config()["control_plane_root"]).expanduser()
    base = Path(home).expanduser() if home is not None else configured_root
    return (base / control_plane_id(project_id)).resolve()


def session_database_path(
    project_id: str, *, home: str | Path | None = None
) -> Path:
    """返回 Session DB 的正式路径。"""

    return control_plane_root(project_id, home=home) / "sessions.sqlite3"


def require_session_database(
    project_id: str, *, home: str | Path | None = None
) -> Path:
    """v7 已绑定项目缺失历史时必须阻止，绝不静默重建。"""

    database = session_database_path(project_id, home=home)
    if not database.is_file():
        raise RuntimeStorageError("RUNTIME_HISTORY_MISSING")
    return database


def initialize_control_plane(
    project_id: str, *, home: str | Path | None = None
) -> Path:
    """仅供显式迁移或创建流程调用，建立受控目录结构。"""

    root = control_plane_root(project_id, home=home)
    for directory in (root, root / "tool-results", root / "checkpoints", root / "locks"):
        directory.mkdir(parents=True, exist_ok=True)
    return root


def inspect_rebind(store: SessionStore, session_id: str, project_root: str | Path) -> dict[str, Any]:
    """只读检查项目路径变化；绝不因检测而重绑。"""

    session = store.get_session(session_id)
    target = str(Path(project_root).resolve())
    return {
        "session_id": session_id,
        "current_project_root": session.project_root,
        "requested_project_root": target,
        "status": "CURRENT" if session.project_root == target else "PROJECT_ROOT_REBIND_REQUIRED",
    }


def apply_rebind(store: SessionStore, session_id: str, project_root: str | Path) -> dict[str, Any]:
    """仅在调用者显式确认后写入新路径，返回可验证记录。"""

    inspection = inspect_rebind(store, session_id, project_root)
    if inspection["status"] == "CURRENT":
        return inspection
    with store.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE sessions SET project_root=?, updated_at=? WHERE session_id=?",
            (inspection["requested_project_root"], utc_now(), session_id),
        )
    inspection["status"] = "REBOUND"
    return inspection
