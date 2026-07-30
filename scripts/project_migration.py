"""project.yaml schema v3/v4/v5 到 v6 的可审计迁移工具。"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_state import (
    ProjectStateError,
    SUPPORTED_SCHEMA_VERSIONS,
    load_project_state,
    parse_project_yaml,
    validate_project_state,
    v3_compatibility_view,
    write_project_state_atomic,
)


TARGET_SCHEMA_VERSION = 6
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


def _next_record_path(project_root: Path) -> Path:
    directory = project_root / "memory" / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    number = len(list(directory.glob("migration-*.json"))) + 1
    return directory / f"migration-{number:03d}.json"


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
) -> dict[str, Any]:
    """回滚前再保存当前 v6；所有备份都保留，不静默删除。"""
    path = Path(project_yaml).resolve()
    source = Path(migration_backup).resolve()
    rollback_backup = Path(pre_rollback_backup).resolve()
    if not source.is_file():
        raise ProjectStateError("迁移备份不存在")
    if rollback_backup.exists():
        raise ProjectStateError("回滚前备份已存在，禁止覆盖")
    old_state = parse_project_yaml(source.read_text(encoding="utf-8"))
    old_errors = validate_project_state(old_state)
    if old_errors:
        raise ProjectStateError("迁移备份无效：" + "; ".join(old_errors))
    rollback_backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, rollback_backup)
    write_project_state_atomic(path, old_state)
    restored = load_project_state(path)
    return {
        "result": "PASS",
        "restored_schema_version": restored["schema_version"],
        "migration_backup": str(source),
        "pre_rollback_backup": str(rollback_backup),
    }


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="AI Development Team project schema 迁移")
    parser.add_argument("action", choices=("check", "preview", "migrate", "verify", "rollback"))
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
