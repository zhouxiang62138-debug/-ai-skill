"""project.yaml schema v3-v6 的可审计迁移工具。

旧入口继续提供 v3-v5 到 v6 的兼容迁移；F10 新入口显式迁移到带 Runtime
投影的 v7。任何预览操作都不会写回旧项目。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .project_state import (
        ProjectStateError,
        SUPPORTED_SCHEMA_VERSIONS,
        load_project_state,
        parse_project_yaml,
        validate_project_state,
        v3_compatibility_view,
        write_project_state_atomic,
        _write_runtime_project_state_atomic,
    )
except ImportError:  # 兼容直接执行 python scripts/project_migration.py
    from project_state import (
        ProjectStateError,
        SUPPORTED_SCHEMA_VERSIONS,
        load_project_state,
        parse_project_yaml,
        validate_project_state,
        v3_compatibility_view,
        write_project_state_atomic,
        _write_runtime_project_state_atomic,
    )


TARGET_SCHEMA_VERSION = 6
RUNTIME_SCHEMA_VERSION = 7
GOVERNANCE_DEFAULTS: dict[str, Any] = {
    "iteration_sequence": 1,
    "automatic_retry_allowed": True,
    "last_issue_package": None,
    "last_generator_response": None,
    "evidence_manifest": None,
    "iteration_metrics": None,
    "retry_history": [],
    "routing_disagreements": [],
    "escalation_record": None,
    "decision_summary_record": None,
}


def inspect_migration(state: dict[str, Any]) -> dict[str, Any]:
    version = state.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return {
            "status": "BLOCKED",
            "reason": f"unsupported_schema_version:{version}",
        }
    if version == TARGET_SCHEMA_VERSION:
        return {"status": "CURRENT", "reason": "already_v6"}
    if state.get("status") == "ARCHIVED":
        return {"status": "READ_ONLY", "reason": "archived_do_not_migrate"}
    if version == 3 and state.get("status") in {
        "APPROVED_FOR_IMPLEMENTATION",
        "PLANNING_COMPLETE",
        "IMPLEMENTING",
        "EVALUATING",
        "ACCEPTED",
    }:
        return {
            "status": "MANUAL_REVIEW",
            "reason": "active_v3_source_chain_requires_review",
        }
    return {
        "status": "ELIGIBLE",
        "reason": f"compatible_additive_upgrade_v{version}_to_v6",
    }


def preview_migration(state: dict[str, Any]) -> dict[str, Any]:
    assessment = inspect_migration(state)
    if assessment["status"] == "CURRENT":
        return copy.deepcopy(state)
    if assessment["status"] != "ELIGIBLE":
        raise ProjectStateError(f"项目不能自动迁移：{assessment['reason']}")
    source_version = state["schema_version"]
    migrated = v3_compatibility_view(state)
    migrated = copy.deepcopy(migrated)
    migrated["schema_version"] = TARGET_SCHEMA_VERSION
    migrated.setdefault("project_type", "application")
    for field, default in GOVERNANCE_DEFAULTS.items():
        migrated.setdefault(field, copy.deepcopy(default))
    if (
        migrated.get("current_iteration") == 5
        or migrated.get("status")
        in {"WAITING_FOR_USER", "BLOCKED", "ACCEPTED", "ARCHIVED"}
    ):
        migrated["automatic_retry_allowed"] = False
    migrated["schema_migration"] = {
        "from_version": source_version,
        "to_version": TARGET_SCHEMA_VERSION,
        "preview_only": True,
    }
    errors = validate_project_state(migrated)
    if errors:
        raise ProjectStateError("迁移预览未通过 v6 校验：" + "; ".join(errors))
    return migrated


def verify_migration(project_yaml: str | Path) -> dict[str, Any]:
    path = Path(project_yaml).resolve()
    state = load_project_state(path)
    errors = validate_project_state(state, path.parent)
    return {
        "valid": not errors and state.get("schema_version") == TARGET_SCHEMA_VERSION,
        "schema_version": state.get("schema_version"),
        "errors": errors,
        "migration_record": state.get("schema_migration_record"),
    }


def _runtime_session_id(state: dict[str, Any], project_root: str | Path | None) -> str:
    root = str(Path(project_root).resolve()) if project_root is not None else "<preview>"
    raw = f"{state.get('project_id')}|{root}|schema-v7".encode("utf-8")
    return f"session-{hashlib.sha256(raw).hexdigest()[:24]}"


def _control_plane_id(project_id: str) -> str:
    """运行时绑定只保存稳定标识，不保存控制平面历史或绝对路径。"""

    return f"runtime-{hashlib.sha256(project_id.encode('utf-8')).hexdigest()[:24]}"


def preview_runtime_migration(
    state: dict[str, Any],
    *,
    project_root: str | Path | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """只读预览 v3-v6 到 v7，不修改源对象或项目文件。"""

    version = state.get("schema_version")
    if version == RUNTIME_SCHEMA_VERSION:
        return copy.deepcopy(state)
    if version not in {3, 4, 5, 6}:
        raise ProjectStateError(f"不支持迁移到 v7 的版本：{version}")
    if state.get("status") == "ARCHIVED":
        raise ProjectStateError("归档项目保持只读，不自动迁移到 v7")
    if version == 3 and state.get("status") in {
        "APPROVED_FOR_IMPLEMENTATION",
        "PLANNING_COMPLETE",
        "IMPLEMENTING",
        "EVALUATING",
        "ACCEPTED",
    }:
        raise ProjectStateError("活动 v3 项目来源链需要人工复核")
    if version == 6:
        migrated = copy.deepcopy(state)
    else:
        migrated = preview_migration(state)
    migrated["schema_version"] = RUNTIME_SCHEMA_VERSION
    migrated["runtime"] = {
        "session_id": session_id
        or _runtime_session_id(state, project_root),
        "control_plane_id": _control_plane_id(str(state["project_id"])),
        "revision": 0,
    }
    migrated["schema_migration"] = {
        "from_version": version,
        "to_version": RUNTIME_SCHEMA_VERSION,
        "preview_only": True,
    }
    errors = validate_project_state(migrated)
    if errors:
        raise ProjectStateError("迁移预览未通过 v7 校验：" + "; ".join(errors))
    return migrated


def verify_runtime_migration(project_yaml: str | Path) -> dict[str, Any]:
    """验证 v7 项目投影。"""

    path = Path(project_yaml).resolve()
    state = load_project_state(path)
    errors = validate_project_state(state, path.parent)
    return {
        "valid": not errors and state.get("schema_version") == RUNTIME_SCHEMA_VERSION,
        "schema_version": state.get("schema_version"),
        "runtime": state.get("runtime"),
        "errors": errors,
        "migration_record": state.get("schema_migration_record"),
    }


def migrate_project_to_v7(
    project_yaml: str | Path,
    backup_path: str | Path,
    *,
    session_id: str | None = None,
    control_plane_home: str | Path | None = None,
) -> dict[str, Any]:
    """创建备份和追加迁移记录后显式迁移到 v7。"""

    path = Path(project_yaml).resolve()
    backup = Path(backup_path).resolve()
    state = load_project_state(path)
    if state.get("schema_version") == RUNTIME_SCHEMA_VERSION:
        return {
            "result": "PASS",
            "changed": False,
            "reason": "already_v7",
            "project_yaml": str(path),
        }
    if backup.exists():
        raise ProjectStateError("迁移备份路径已存在，禁止覆盖")
    preview = preview_runtime_migration(
        state, project_root=path.parent, session_id=session_id
    )
    record_path = _next_record_path(path.parent)
    record = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from_version": state["schema_version"],
        "to_version": RUNTIME_SCHEMA_VERSION,
        "project_yaml": "project.yaml",
        "backup_path": str(backup),
        "session_id": preview["runtime"]["session_id"],
        "status": "PREPARING",
    }
    _write_migration_record(record_path, record)
    try:
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        record["status"] = "BACKUP_CREATED"
        _write_migration_record(record_path, record)
    # 先建立独立 Session Store，再提交 YAML 投影；若中断，遗留 DB 可由
    # inspect/recover 发现，绝不把 Session 历史塞回 project.yaml。
        repository_root = Path(__file__).resolve().parents[1]
        if str(repository_root) not in sys.path:
            sys.path.insert(0, str(repository_root))
        from runtime.session_store import SessionStore
        from runtime.control_plane import initialize_control_plane

        control_plane = initialize_control_plane(
            str(state["project_id"]), home=control_plane_home
        )
        record["status"] = "CONTROL_PLANE_CREATED"
        _write_migration_record(record_path, record)
        store = SessionStore(control_plane / "sessions.sqlite3")
        store.create_session(
            str(state["project_id"]), path.parent,
            idempotency_key=f"runtime-migration:{preview['runtime']['session_id']}",
            session_id=str(preview["runtime"]["session_id"]),
        )
        preview["schema_migration"]["preview_only"] = False
        preview["schema_migration_record"] = record_path.relative_to(path.parent).as_posix()
        _write_runtime_project_state_atomic(path, preview)
        record["status"] = "PROJECT_BOUND"
        _write_migration_record(record_path, record)
        verification = verify_runtime_migration(path)
        if not verification["valid"]:
            raise ProjectStateError("迁移后 v7 验证失败：" + "; ".join(verification["errors"]))
        record["status"] = "COMMITTED"
        _write_migration_record(record_path, record)
    except Exception:
        record["status"] = "RECOVERY_REQUIRED"
        _write_migration_record(record_path, record)
        raise
    return {
        "result": "PASS",
        "changed": True,
        "project_yaml": str(path),
        "backup_path": str(backup),
        "record_path": str(record_path),
        "verification": verification,
    }


def _next_record_path(project_root: Path) -> Path:
    directory = project_root / "memory" / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    number = len(list(directory.glob("migration-*.json"))) + 1
    return directory / f"migration-{number:03d}.json"


def _write_migration_record(path: Path, record: dict[str, Any]) -> None:
    """原子更新迁移生命周期记录；成功状态只能在验证后写入。"""

    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def migrate_project_file(
    project_yaml: str | Path,
    backup_path: str | Path,
) -> dict[str, Any]:
    """创建显式备份后迁移；重复调用 v6 项目返回幂等结果。"""
    path = Path(project_yaml).resolve()
    backup = Path(backup_path).resolve()
    state = load_project_state(path)
    assessment = inspect_migration(state)
    if assessment["status"] == "CURRENT":
        return {
            "result": "PASS",
            "changed": False,
            "reason": "already_v6",
            "project_yaml": str(path),
        }
    if assessment["status"] != "ELIGIBLE":
        raise ProjectStateError(f"项目不能自动迁移：{assessment['reason']}")
    if backup.exists():
        raise ProjectStateError("迁移备份路径已存在，禁止覆盖")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup)
    preview = preview_migration(state)
    record_path = _next_record_path(path.parent)
    record = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from_version": state["schema_version"],
        "to_version": TARGET_SCHEMA_VERSION,
        "project_yaml": "project.yaml",
        "backup_path": str(backup),
        "status": "MIGRATED",
    }
    record_temp = record_path.with_suffix(".json.tmp")
    record_temp.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record_temp.replace(record_path)
    preview["schema_migration"]["preview_only"] = False
    preview["schema_migration_record"] = record_path.relative_to(path.parent).as_posix()
    write_project_state_atomic(path, preview)
    verification = verify_migration(path)
    if not verification["valid"]:
        raise ProjectStateError("迁移后验证失败：" + "; ".join(verification["errors"]))
    return {
        "result": "PASS",
        "changed": True,
        "project_yaml": str(path),
        "backup_path": str(backup),
        "record_path": str(record_path),
        "verification": verification,
    }


def rollback_project_file(
    project_yaml: str | Path,
    migration_backup: str | Path,
    pre_rollback_backup: str | Path,
    *,
    control_plane_home: str | Path | None = None,
) -> dict[str, Any]:
    """回滚前再保存当前 v6；所有备份都保留，不静默删除。"""
    path = Path(project_yaml).resolve()
    source = Path(migration_backup).resolve()
    rollback_backup = Path(pre_rollback_backup).resolve()
    if not source.is_file():
        raise ProjectStateError("迁移备份不存在")
    if rollback_backup.exists():
        raise ProjectStateError("回滚前备份已存在，禁止覆盖")
    current_state = load_project_state(path)
    old_state = parse_project_yaml(source.read_text(encoding="utf-8"))
    old_errors = validate_project_state(old_state)
    if old_errors:
        raise ProjectStateError("迁移备份无效：" + "; ".join(old_errors))
    rollback_backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, rollback_backup)
    if current_state.get("schema_version") == RUNTIME_SCHEMA_VERSION:
        _write_runtime_project_state_atomic(path, old_state)
    else:
        write_project_state_atomic(path, old_state)
    if current_state.get("schema_version") == RUNTIME_SCHEMA_VERSION:
        from runtime.control_plane import session_database_path
        from runtime.session_store import SessionStore

        database = session_database_path(str(current_state["project_id"]), home=control_plane_home)
        if database.is_file():
            store = SessionStore(database)
            store.set_session_status(str(current_state["runtime"]["session_id"]), "DETACHED")
    restored = load_project_state(path)
    return {
        "result": "PASS",
        "restored_schema_version": restored["schema_version"],
        "migration_backup": str(source),
        "pre_rollback_backup": str(rollback_backup),
        "detached_session_id": (current_state.get("runtime") or {}).get("session_id"),
    }


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="AI Development Team project schema 迁移")
    parser.add_argument(
        "action",
        choices=(
            "check", "preview", "migrate", "verify", "rollback",
            "runtime-preview", "runtime-migrate", "runtime-verify",
        ),
    )
    parser.add_argument("project_yaml", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--pre-rollback-backup", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "check":
            result = inspect_migration(load_project_state(args.project_yaml))
        elif args.action == "preview":
            result = preview_migration(load_project_state(args.project_yaml))
        elif args.action == "verify":
            result = verify_migration(args.project_yaml)
        elif args.action == "runtime-preview":
            result = preview_runtime_migration(
                load_project_state(args.project_yaml),
                project_root=args.project_yaml.parent,
            )
        elif args.action == "runtime-verify":
            result = verify_runtime_migration(args.project_yaml)
        elif args.action == "runtime-migrate":
            if args.backup is None:
                parser.error("runtime-migrate 必须提供 --backup")
            result = migrate_project_to_v7(args.project_yaml, args.backup)
        elif args.action == "migrate":
            if args.backup is None:
                parser.error("migrate 必须提供 --backup")
            result = migrate_project_file(args.project_yaml, args.backup)
        else:
            if args.backup is None or args.pre_rollback_backup is None:
                parser.error("rollback 必须提供 --backup 和 --pre-rollback-backup")
            result = rollback_project_file(
                args.project_yaml, args.backup, args.pre_rollback_backup
            )
    except (OSError, ProjectStateError) as exc:
        print(json.dumps({"result": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
