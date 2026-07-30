"""Skill 维护项目的确定性路径授权和受控同步门禁。"""

from __future__ import annotations

import hashlib
import fnmatch
import argparse
import os
import re
import shutil
import tempfile
import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_state import (
    ProjectStateError,
    load_project_state,
    write_project_state_atomic,
)


WINDOWS_SYMLINK_BLOCK_REASON = "windows_symlink_privilege_missing"
WINDOWS_SYMLINK_RESUME_STATUS = "EVALUATING"
WINDOWS_SYMLINK_RESUME_ROLE = "evaluator"
SYNC_REPORT_REQUIRED_FIELDS = (
    "sync_id",
    "project_id",
    "project_root",
    "source_repository",
    "destination_repository",
    "started_at",
    "finished_at",
    "final_evaluation",
    "pre_sync_diff",
    "unknown_install_changes",
    "backup",
    "expected_files",
    "copied",
    "unchanged",
    "excluded",
    "failed_files",
    "rollback_triggered",
    "rollback_result",
    "post_sync_diff",
    "installation_validation",
    "result",
    "reason",
    "blocker",
)


@dataclass(frozen=True)
class RepositoryRef:
    """受控仓库内引用；绝不把外部绝对路径当作普通工件字符串。"""

    repository: str
    path: str


@dataclass(frozen=True)
class RollbackResult:
    """回滚执行与复核的确定性结果。"""

    success: bool
    restored: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "restored": list(self.restored),
            "removed": list(self.removed),
            "missing": list(self.missing),
            "extra": list(self.extra),
            "mismatched": list(self.mismatched),
            "error": self.error,
        }


class SnapshotError(ProjectStateError):
    """快照未成为 VALID，禁止进入提交阶段。"""

    def __init__(self, stage: str, reason: str, backup_path: Path | None = None):
        super().__init__(f"快照失败[{stage}]：{reason}")
        self.stage = stage
        self.reason = reason
        self.backup_path = backup_path


class SyncTransactionError(ProjectStateError):
    """同步失败；携带实际结果和确定性候选项目状态。"""

    def __init__(self, reason: str, result: dict[str, Any], project_state: dict[str, Any]):
        super().__init__(reason)
        self.result = result
        self.project_state = project_state


@dataclass(frozen=True)
class InstallationValidation:
    success: bool
    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()
    load_check: str = "NOT_RUN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "missing": list(self.missing),
            "mismatched": list(self.mismatched),
            "unexpected": list(self.unexpected),
            "forbidden": list(self.forbidden),
            "load_check": self.load_check,
        }


def probe_windows_symlink_capability() -> dict[str, Any]:
    """真实创建目录和文件符号链接；不使用 mock，不访问安装副本。"""
    result: dict[str, Any] = {
        "directory_symlink": False,
        "file_symlink": False,
        "errors": [],
    }
    with tempfile.TemporaryDirectory(prefix="skill-symlink-probe-") as directory:
        root = Path(directory)
        outside_directory = root / "outside-directory"
        outside_directory.mkdir()
        outside_file = root / "outside-file.txt"
        outside_file.write_text("probe", encoding="utf-8")
        directory_link = root / "directory-link"
        file_link = root / "file-link.txt"
        try:
            os.symlink(outside_directory, directory_link, target_is_directory=True)
            result["directory_symlink"] = directory_link.is_symlink()
        except OSError as exc:
            result["errors"].append(
                {"kind": "directory", "winerror": getattr(exc, "winerror", None), "message": str(exc)}
            )
        try:
            os.symlink(outside_file, file_link, target_is_directory=False)
            result["file_symlink"] = file_link.is_symlink()
        except OSError as exc:
            result["errors"].append(
                {"kind": "file", "winerror": getattr(exc, "winerror", None), "message": str(exc)}
            )
    result["available"] = bool(
        result["directory_symlink"] and result["file_symlink"]
    )
    return result


def apply_symlink_environment_block(state: dict[str, Any]) -> dict[str, Any]:
    """从最终验收或同原因 BLOCKED 确定性进入环境阻塞。"""
    if state.get("status") == "BLOCKED":
        if state.get("blocked_reason") != WINDOWS_SYMLINK_BLOCK_REASON:
            raise ProjectStateError("当前 BLOCKED 原因不是 Windows 符号链接权限")
    elif not (
        state.get("status") == WINDOWS_SYMLINK_RESUME_STATUS
        and state.get("next_role") == WINDOWS_SYMLINK_RESUME_ROLE
    ):
        raise ProjectStateError("符号链接环境阻塞只能从 EVALUATING/evaluator 进入")
    updated = dict(state)
    updated.update(
        status="BLOCKED",
        next_role=None,
        f8_5_status="BLOCKED",
        blocked_reason=WINDOWS_SYMLINK_BLOCK_REASON,
        blocked_context={
            "category": "acceptance_environment",
            "resume_status": WINDOWS_SYMLINK_RESUME_STATUS,
            "resume_next_role": WINDOWS_SYMLINK_RESUME_ROLE,
            "required_checks": [
                "test_directory_symlink_escape_is_rejected",
                "test_file_symlink_escape_is_rejected",
            ],
        },
    )
    return updated


def resume_symlink_environment_block(
    state: dict[str, Any],
    capability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """权限恢复后只返回正式最终验收态；绝不直接返回 PASS/ACCEPTED。"""
    if state.get("status") != "BLOCKED":
        raise ProjectStateError("当前项目不在 BLOCKED")
    if state.get("blocked_reason") != WINDOWS_SYMLINK_BLOCK_REASON:
        raise ProjectStateError("阻塞原因不是 windows_symlink_privilege_missing")
    context = state.get("blocked_context")
    if not isinstance(context, dict) or (
        context.get("resume_status") != WINDOWS_SYMLINK_RESUME_STATUS
        or context.get("resume_next_role") != WINDOWS_SYMLINK_RESUME_ROLE
    ):
        raise ProjectStateError("缺少确定性的符号链接阻塞恢复上下文")
    checked = capability or probe_windows_symlink_capability()
    if not checked.get("available"):
        raise ProjectStateError("真实文件和目录符号链接权限仍不可用")
    updated = dict(state)
    updated.update(
        status=WINDOWS_SYMLINK_RESUME_STATUS,
        next_role=WINDOWS_SYMLINK_RESUME_ROLE,
        f8_5_status="FAIL",
        blocked_reason=None,
        blocked_context=None,
        final_evaluation_status=None,
        required_tests_status=None,
    )
    return updated


def persist_symlink_environment_block(project_yaml: str | Path) -> dict[str, Any]:
    from project_state import load_project_state

    path = Path(project_yaml)
    updated = apply_symlink_environment_block(load_project_state(path))
    write_project_state_atomic(path, updated)
    return updated


def persist_symlink_environment_resume(project_yaml: str | Path) -> dict[str, Any]:
    from project_state import load_project_state

    path = Path(project_yaml)
    state = load_project_state(path)
    updated = resume_symlink_environment_block(state)
    write_project_state_atomic(path, updated)
    return updated


def normalize_windows_path(value: str | Path) -> Path:
    """拒绝 UNC、长路径前缀、穿越和符号链接逃逸前的可疑文本。"""
    raw = str(value).replace("/", "\\")
    if raw.startswith("\\\\") or raw.startswith("\\\\?\\"):
        raise ProjectStateError("不允许 UNC 或长路径前缀")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise ProjectStateError("必须使用无路径穿越的绝对路径")
    try:
        return Path(os.path.normcase(os.path.normpath(str(path)))).resolve()
    except OSError as exc:
        raise ProjectStateError("无法安全解析真实路径，默认拒绝") from exc


def _target(state: dict[str, Any], name: str) -> Path:
    if (
        state.get("schema_version") not in {5, 6}
        or state.get("project_type") != "skill_maintenance"
    ):
        raise ProjectStateError("当前项目不是 skill_maintenance")
    target = (state.get("targets") or {}).get(name)
    if not isinstance(target, dict) or not isinstance(target.get("path"), str):
        raise ProjectStateError(f"未声明目标仓库：{name}")
    try:
        raw = Path(target["path"])
        lexical = Path(os.path.normcase(os.path.abspath(os.path.normpath(str(raw)))))
        resolved = normalize_windows_path(target["path"])
        if lexical != resolved:
            raise ProjectStateError("声明的仓库根不得是符号链接、Junction 或目录别名")
        # 安全比较使用规范化路径，实际 I/O 保留声明路径的原始大小写。
        return raw.resolve()
    except ProjectStateError as exc:
        raise ProjectStateError(f"目标仓库路径不安全：{name}") from exc


def validate_target_layout(
    state: dict[str, Any], project_root: str | Path | None = None
) -> list[str]:
    errors: list[str] = []
    try:
        working = _target(state, "working_repository")
        installed = _target(state, "installed_repository")
    except ProjectStateError as exc:
        return [str(exc)]
    if working == installed:
        errors.append("工作副本与安装副本解析为同一路径")
    else:
        try:
            installed.relative_to(working)
            errors.append("安装副本不得位于工作副本内部")
        except ValueError:
            pass
        try:
            working.relative_to(installed)
            errors.append("工作副本不得位于安装副本内部")
        except ValueError:
            pass
    if project_root is not None:
        project = Path(project_root).resolve()
        for label, target in (
            ("working_repository", working),
            ("installed_repository", installed),
        ):
            try:
                target.relative_to(project)
                errors.append(f"{label} 不得位于项目目录内部")
            except ValueError:
                pass
            try:
                project.relative_to(target)
                errors.append(f"项目目录不得位于 {label} 内部")
            except ValueError:
                pass
    return errors


def _controlled_install_destination(
    state: dict[str, Any], candidate: str | Path
) -> Path:
    """仅供同步服务使用；每次写前重新解析，防止链接在校验后被替换。"""
    root = _target(state, "installed_repository")
    normalized_root = Path(os.path.normcase(os.path.normpath(str(root))))
    normalized_target = normalize_windows_path(candidate)
    try:
        normalized_target.relative_to(normalized_root)
    except ValueError as exc:
        raise ProjectStateError("同步目标逃出安装副本白名单") from exc
    return Path(candidate).resolve()


def authorize_target_path(state: dict[str, Any], name: str, candidate: str | Path, *, write: bool) -> Path:
    root = _target(state, name)
    normalized_root = Path(os.path.normcase(os.path.normpath(str(root))))
    normalized_target = normalize_windows_path(candidate)
    try:
        normalized_target.relative_to(normalized_root)
    except ValueError as exc:
        raise ProjectStateError("外部路径未在已声明目标仓库白名单内") from exc
    if write and name != "working_repository":
        raise ProjectStateError("安装副本在同步门禁前后均不可由 Generator 直接写入")
    return Path(candidate).resolve()


def resolve_repository_ref(state: dict[str, Any], reference: RepositoryRef, *, write: bool = False) -> Path:
    if reference.repository == "project":
        if write:
            raise ProjectStateError("项目工件写入由项目角色策略控制")
        raise ProjectStateError("项目引用必须由项目根目录解析")
    if reference.repository not in {"working_repository", "installed_repository"}:
        raise ProjectStateError("未知 repository 引用")
    relative = Path(reference.path)
    if relative.is_absolute() or ".." in relative.parts or reference.path.startswith("\\"):
        raise ProjectStateError("repository 引用必须是仓库内部相对路径")
    return authorize_target_path(state, reference.repository, _target(state, reference.repository) / relative, write=write)


def resolve_artifact_ref(
    state: dict[str, Any],
    project_root: str | Path,
    reference: dict[str, Any],
    *,
    role: str,
    write: bool = False,
) -> Path:
    """解析正式 Handoff/Evidence 引用，并执行角色写权限。"""
    if not isinstance(reference, dict):
        raise ProjectStateError("artifact_ref 必须是对象")
    repository = reference.get("repository")
    raw_path = reference.get("path")
    if repository not in {"project", "working_repository", "installed_repository"}:
        raise ProjectStateError("artifact_ref.repository 未知")
    if not isinstance(raw_path, str) or not raw_path:
        raise ProjectStateError("artifact_ref.path 必须是非空相对路径")
    if raw_path.startswith(("\\", "/")) or re.match(r"^[A-Za-z]:", raw_path):
        raise ProjectStateError("artifact_ref.path 不得是绝对路径")
    relative = Path(raw_path.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ProjectStateError("artifact_ref.path 存在路径穿越")
    if write and role == "planner":
        raise ProjectStateError("Planner 不得写入目标仓库")
    if write and role == "evaluator":
        raise ProjectStateError("Evaluator 不得写入目标仓库")
    if write and repository == "installed_repository":
        raise ProjectStateError("Generator 不得声明修改安装副本")
    if repository == "project":
        root = Path(project_root).resolve()
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ProjectStateError("项目工件引用逃出项目根目录") from exc
        return candidate
    return resolve_repository_ref(
        state, RepositoryRef(repository, relative.as_posix()), write=write
    )


def validate_handoff_record(
    state: dict[str, Any],
    project_root: str | Path,
    handoff: dict[str, Any],
    *,
    role: str,
) -> list[str]:
    errors: list[str] = []
    fields = (
        "changed_project_artifacts",
        "changed_target_files",
        "verification_artifacts",
    )
    for field in fields:
        values = handoff.get(field)
        if not isinstance(values, list):
            errors.append(f"{field} 必须是列表")
            continue
        for index, reference in enumerate(values):
            try:
                repository = reference.get("repository") if isinstance(reference, dict) else None
                if field == "changed_project_artifacts" and repository != "project":
                    raise ProjectStateError("项目工件修改必须引用 project")
                if field == "changed_target_files" and repository != "working_repository":
                    raise ProjectStateError("目标文件修改只能引用 working_repository")
                resolve_artifact_ref(
                    state,
                    project_root,
                    reference,
                    role=role,
                    write=field != "verification_artifacts",
                )
            except ProjectStateError as exc:
                errors.append(f"{field}[{index}]：{exc}")
    return errors


def validate_evidence_record(
    state: dict[str, Any],
    project_root: str | Path,
    evidence: dict[str, Any],
    *,
    role: str,
) -> list[str]:
    errors: list[str] = []
    try:
        evidence_path = evidence.get("evidence_file")
        if not isinstance(evidence_path, dict) or evidence_path.get("repository") != "project":
            raise ProjectStateError("证据文件自身必须保存在 project")
        resolve_artifact_ref(
            state, project_root, evidence_path, role=role, write=False
        )
        resolve_artifact_ref(
            state, project_root, evidence.get("target"), role=role, write=False
        )
    except ProjectStateError as exc:
        errors.append(str(exc))
    digest = evidence.get("target_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        errors.append("target_sha256 必须是小写 SHA-256")
    if not isinstance(evidence.get("command"), str) or not evidence["command"].strip():
        errors.append("command 不能为空")
    if evidence.get("result") not in {"PASS", "FAIL", "BLOCKED"}:
        errors.append("result 枚举无效")
    if role == "generator" and isinstance(evidence.get("target"), dict):
        if evidence["target"].get("repository") == "installed_repository" and evidence.get("write_intent"):
            errors.append("Generator 不得生成安装副本写入证据")
    return errors


# F9：返工协议、可复验证据与受控重试治理。
REWORK_ROUTES = {
    "implementation_issue": ("IMPLEMENTING", "generator", None),
    "testing_issue": ("IMPLEMENTING", "generator", None),
    "product_scope_issue": ("PLANNING", "planner", None),
    "requirement_issue": ("INTAKE", None, "first_ask_intake"),
}


def validate_rework_record(record: dict[str, Any]) -> list[str]:
    """校验追加式返工记录，拒绝含糊路由和缺失的可复验依据。"""
    errors: list[str] = []
    failure_class = record.get("failure_class")
    if record.get("schema_version") != 1:
        errors.append("返工记录 schema_version 必须为 1")
    if failure_class not in REWORK_ROUTES:
        errors.append("failure_class 未声明或不允许自动返工")
    elif record.get("target_role") != REWORK_ROUTES[failure_class][1]:
        errors.append("target_role 与 failure_class 的确定性路由不一致")
    for field in ("source_evaluation", "required_evidence", "summary"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            errors.append(f"{field} 必须是非空字符串")
    reference = record.get("record_reference")
    if not isinstance(reference, str) or not re.fullmatch(
        r"memory/handoffs/rework-\d{3}\.md", reference
    ):
        errors.append("record_reference 必须指向追加式返工记录")
    if not isinstance(record.get("retry_number"), int) or record["retry_number"] < 1:
        errors.append("retry_number 必须是正整数")
    if not isinstance(record.get("affected_artifacts"), list):
        errors.append("affected_artifacts 必须是列表")
    return errors


def validate_reproducible_evidence(
    state: dict[str, Any], record: dict[str, Any], project_root: str | Path
) -> list[str]:
    """验证证据目标、真实哈希与命令，禁止用声明哈希替代实际文件。"""
    errors: list[str] = []
    if record.get("schema_version") != 2:
        errors.append("可复现证据 schema_version 必须为 2")
    for field in ("evidence_id", "captured_at"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            errors.append(f"{field} 必须是非空字符串")
    if isinstance(record.get("evidence_id"), str) and not re.fullmatch(
        r"evidence-\d{3}", record["evidence_id"]
    ):
        errors.append("evidence_id 格式无效")
    if isinstance(record.get("captured_at"), str) and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})",
        record["captured_at"],
    ):
        errors.append("captured_at 必须是带时区的 ISO-8601 时间")
    if not isinstance(record.get("command_exit_code"), int):
        errors.append("command_exit_code 必须是整数")
    elif record.get("result") == "PASS" and record["command_exit_code"] != 0:
        errors.append("PASS 证据的 command_exit_code 必须为 0")
    if errors:
        return errors
    errors = validate_evidence_record(state, project_root, record, role="evaluator")
    if errors:
        return errors
    try:
        target = resolve_artifact_ref(
            state, project_root, record["target"], role="evaluator"
        )
        if not target.is_file():
            return ["证据目标必须是存在的普通文件"]
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != record["target_sha256"]:
            return ["target_sha256 与证据目标实际哈希不一致"]
    except (KeyError, ProjectStateError) as exc:
        return [str(exc)]
    return []


def apply_rework_retry(state: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """按唯一计数点路由一次返工；第五次后必须等待用户。"""
    errors = validate_rework_record(record)
    if errors:
        raise ProjectStateError("返工记录无效：" + "; ".join(errors))
    if state.get("status") != "EVALUATING":
        raise ProjectStateError("只有 Evaluator 可发起受控返工")
    iteration = state.get("current_iteration")
    if not isinstance(iteration, int) or iteration < 0:
        raise ProjectStateError("current_iteration 无效")
    if record["retry_number"] != iteration + 1:
        raise ProjectStateError("retry_number 必须等于 current_iteration + 1")
    if state.get("rework_record") == record.get("record_reference"):
        raise ProjectStateError("同一返工记录不得重复计数")
    updated = dict(state)
    updated["rework_record"] = record.get("record_reference")
    if iteration >= 4:
        updated.update(current_iteration=5, status="WAITING_FOR_USER", next_role=None,
                       blocked_reason="maximum_iterations_reached")
        return updated
    status, role, module = REWORK_ROUTES[record["failure_class"]]
    updated.update(current_iteration=iteration + 1, status=status, next_role=role,
                   active_module=module, blocked_reason=None)
    return updated


def repository_manifest(root: str | Path, exclusions: list[str]) -> dict[str, str]:
    base = Path(root).resolve()
    result: dict[str, str] = {}
    for item in base.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(base).as_posix()
        if any(
            relative == rule
            or relative.startswith(rule.rstrip("*").rstrip("/") + "/")
            or fnmatch.fnmatchcase(relative, rule)
            for rule in exclusions
        ):
            continue
        result[relative] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def validate_installed_repository(
    working_root: str | Path,
    installed_root: str | Path,
    exclusions: list[str],
) -> InstallationValidation:
    """验证应同步文件、未知文件、禁入工件和最小 Skill 可加载结构。"""
    working = Path(working_root).resolve()
    installed = Path(installed_root).resolve()
    expected = repository_manifest(working, exclusions)
    actual = repository_manifest(installed, exclusions)
    missing = tuple(sorted(set(expected) - set(actual)))
    unexpected = tuple(sorted(set(actual) - set(expected)))
    mismatched = tuple(
        sorted(key for key in set(expected) & set(actual) if expected[key] != actual[key])
    )
    forbidden_roots = {
        "project.yaml", "planning", "implementation", "evaluation",
        "handoffs", "memory", "logs", "archive", "reports",
    }
    forbidden: list[str] = []
    for item in installed.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(installed).as_posix()
        if relative == "project.yaml" or relative.split("/", 1)[0] in forbidden_roots:
            forbidden.append(relative)
    skill_file = installed / "SKILL.md"
    load_check = "PASS" if skill_file.is_file() and skill_file.read_text(
        encoding="utf-8"
    ).lstrip().startswith("---") else "FAIL"
    success = not (missing or unexpected or mismatched or forbidden) and load_check == "PASS"
    return InstallationValidation(
        success, missing, mismatched, unexpected, tuple(sorted(forbidden)), load_check
    )


def _mark_failed_snapshot(backup: Path, stage: str, reason: str) -> None:
    """尽力标记残留目录；标记失败本身不能掩盖原始错误。"""
    try:
        backup.mkdir(parents=True, exist_ok=True)
        (backup / ".snapshot-invalid.json").write_text(
            json.dumps({"status": "INVALID", "stage": stage, "reason": reason}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def snapshot_repository(root: str | Path, backup_root: str | Path, exclusions: list[str]) -> dict[str, Any]:
    """创建 VALID 快照；任一阶段失败均抛 SnapshotError，禁止提交。"""
    source, backup = Path(root).resolve(), Path(backup_root).resolve()
    stage = "create_directory"
    try:
        backup.mkdir(parents=True, exist_ok=False)
        stage = "calculate_hashes"
        manifest = repository_manifest(source, exclusions)
        stage = "copy_files"
        for relative in sorted(manifest):
            destination = backup / "files" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, destination)
        stage = "verify_copies"
        copied_manifest = repository_manifest(backup / "files", [])
        if copied_manifest != manifest:
            raise OSError("快照副本哈希与源清单不一致")
        manifest_payload = {"status": "VALID", "files": manifest}
        stage = "write_manifest"
        manifest_path = backup / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        stage = "calculate_digest"
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        stage = "write_digest"
        (backup / "manifest.sha256").write_text(digest + "\n", encoding="ascii")
        return {
            "status": "VALID",
            "path": str(backup),
            "files_root": str(backup / "files"),
            "files": manifest,
            "digest": digest,
            "manifest_path": str(manifest_path),
        }
    except (OSError, ValueError) as exc:
        _mark_failed_snapshot(backup, stage, str(exc))
        raise SnapshotError(stage, str(exc), backup) from exc


def restore_snapshot(destination_root: str | Path, snapshot: dict[str, Any], exclusions: list[str]) -> RollbackResult:
    destination = Path(destination_root).resolve()
    files_root = Path(snapshot["files_root"]).resolve()
    before = dict(snapshot["files"])
    restored: list[str] = []
    removed: list[str] = []
    try:
        current = repository_manifest(destination, exclusions)
        for relative in sorted(current):
            if relative not in before:
                (destination / relative).unlink()
                removed.append(relative)
        for relative in sorted(before):
            source, target = files_root / relative, destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.append(relative)
        # 只清理本次新增文件留下的空目录；排除目录及非空目录会保留。
        for directory in sorted(destination.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    except OSError as exc:
        return RollbackResult(False, tuple(restored), tuple(removed), error=str(exc))
    after = repository_manifest(destination, exclusions)
    missing = tuple(sorted(set(before) - set(after)))
    extra = tuple(sorted(set(after) - set(before)))
    mismatched = tuple(sorted(key for key in set(before) & set(after) if before[key] != after[key]))
    return RollbackResult(
        not (missing or extra or mismatched),
        tuple(restored), tuple(removed), missing, extra, mismatched,
    )


def _failed_project_state(state: dict[str, Any], rollback: RollbackResult) -> dict[str, Any]:
    updated = dict(state)
    updated["last_sync_status"] = "FAIL" if rollback.success else "BLOCKED"
    if not rollback.success:
        updated.update(status="BLOCKED", next_role=None, blocked_reason="sync_rollback_failed")
    return updated


def persist_sync_failure_state(
    project_yaml: str | Path,
    state: dict[str, Any],
    rollback: RollbackResult,
) -> dict[str, Any]:
    """原子持久化同步失败状态；回滚失败必须写成 BLOCKED。"""
    updated = _failed_project_state(state, rollback)
    write_project_state_atomic(project_yaml, updated)
    return updated


def verify_sync_gate(
    state: dict[str, Any],
    installed_baseline: dict[str, str],
    project_root: str | Path | None = None,
) -> list[str]:
    errors: list[str] = []
    if project_root is not None:
        project_yaml = Path(project_root).resolve() / "project.yaml"
        if not project_yaml.is_file():
            errors.append("独立项目根目录缺少唯一状态源 project.yaml")
        else:
            try:
                persisted = load_project_state(project_yaml)
                for repository in (
                    "working_repository",
                    "installed_repository",
                ):
                    if _target(state, repository) != _target(
                        persisted, repository
                    ):
                        errors.append(
                            f"{repository} 与 project.yaml 声明不一致"
                        )
            except ProjectStateError as exc:
                errors.append(f"无法校验 project.yaml 目标路径：{exc}")
    if state.get("final_evaluation_status") != "PASS" or state.get("status") != "ACCEPTED":
        errors.append("最终验收未 PASS，禁止同步")
    evaluation_ref = state.get("last_evaluation")
    if not isinstance(evaluation_ref, str) or not evaluation_ref:
        errors.append("缺少最终 Evaluation 报告")
    elif project_root is not None:
        project = Path(project_root).resolve()
        evaluation = (project / evaluation_ref).resolve()
        try:
            evaluation.relative_to(project)
        except ValueError:
            errors.append("最终 Evaluation 报告逃出项目目录")
        else:
            if not evaluation.is_file():
                errors.append("最终 Evaluation 报告不存在")
    if state.get("required_tests_status") != "PASS":
        errors.append("必需测试未全部通过")
    if not isinstance(state.get("sync_preflight_diff"), list):
        errors.append("缺少同步前差异")
    for issue in state.get("open_issues") or []:
        if (
            isinstance(issue, dict)
            and issue.get("severity") in {"blocker", "critical"}
            and issue.get("status") not in {"RESOLVED", "CLOSED"}
        ):
            errors.append("存在未解决的 blocker 或 critical Issue")
            break
    errors.extend(validate_target_layout(state, project_root))
    exclusions = (state.get("sync") or {}).get("exclusions")
    if not isinstance(exclusions, list) or not exclusions:
        return errors + ["缺少同步排除规则"]
    current = repository_manifest(_target(state, "installed_repository"), exclusions)
    if current != installed_baseline:
        errors.append("安装副本存在未知修改，禁止覆盖")
    return errors


def write_sync_report(project_root: str | Path, report: dict[str, Any]) -> tuple[Path, Path]:
    """JSON 与 Markdown 由同一事实对象生成，并嵌入一致性摘要。"""
    root = Path(project_root).resolve()
    directory = root / "reports" / "sync"
    directory.mkdir(parents=True, exist_ok=True)
    number = len(list(directory.glob("sync-*.json"))) + 1
    report = dict(report)
    report.setdefault("sync_id", f"sync-{number:03d}")
    report.setdefault("finished_at", datetime.now(timezone.utc).isoformat())
    defaults = {
        "project_id": None,
        "project_root": str(root),
        "source_repository": None,
        "destination_repository": None,
        "started_at": None,
        "final_evaluation": None,
        "pre_sync_diff": [],
        "unknown_install_changes": [],
        "backup": None,
        "expected_files": [],
        "copied": [],
        "unchanged": [],
        "excluded": [],
        "failed_files": [],
        "rollback_triggered": False,
        "rollback_result": None,
        "post_sync_diff": [],
        "installation_validation": None,
        "result": "BLOCKED",
        "reason": None,
        "blocker": None,
    }
    for field, default in defaults.items():
        report.setdefault(field, default)
    json_path = directory / f"sync-{number:03d}.json"
    md_path = directory / f"sync-{number:03d}.md"
    canonical = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    fact_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    json_temp = json_path.with_suffix(".json.tmp")
    md_temp = md_path.with_suffix(".md.tmp")
    json_temp.write_text(canonical + "\n", encoding="utf-8")
    lines = [
        f"# 同步报告 {report['sync_id']}", "",
        f"结果：{report.get('result', 'BLOCKED')}", "",
        f"<!-- sync-fact-sha256:{fact_digest} -->", "",
        "## 同步事实", "",
        f"- 项目：`{report.get('project_id')}`",
        f"- 项目目录：`{report.get('project_root')}`",
        f"- 工作副本：`{report.get('source_repository')}`",
        f"- 安装副本：`{report.get('destination_repository')}`",
        f"- 最终验收：`{report.get('final_evaluation')}`",
        f"- 开始：`{report.get('started_at')}`",
        f"- 结束：`{report.get('finished_at')}`",
        f"- 备份：`{(report.get('backup') or {}).get('path') if isinstance(report.get('backup'), dict) else report.get('backup')}`",
        f"- 触发回滚：`{report.get('rollback_triggered')}`",
        f"- 原因：`{report.get('reason')}`",
        f"- blocker：`{report.get('blocker')}`",
        "",
        "## 已同步文件",
    ]
    lines.extend(f"- `{item}`" for item in report.get("copied", []))
    lines.extend(["", "## 排除项"])
    lines.extend(f"- `{item}`" for item in report.get("excluded", []))
    lines.extend(["", "## 安装副本验证", "", "```json"])
    lines.append(json.dumps(report.get("installation_validation"), ensure_ascii=False, indent=2))
    lines.extend(["```", "", "## 回滚结果", "", "```json"])
    rollback = report.get("rollback_result", report.get("rollback"))
    lines.append(json.dumps(rollback, ensure_ascii=False, indent=2))
    lines.extend(["```", "", "## 同步前后差异", "", "```json"])
    lines.append(json.dumps({
        "before": report.get("pre_sync_diff", report.get("before")),
        "after": report.get("post_sync_diff", report.get("after")),
        "failed_files": report.get("failed_files", []),
    }, ensure_ascii=False, indent=2))
    lines.extend(["```", "", "## 完整结构化事实", "", "```json"])
    lines.append(canonical)
    lines.append("```")
    md_temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if hashlib.sha256(json_temp.read_text(encoding="utf-8").rstrip("\n").encode("utf-8")).hexdigest() != fact_digest:
        raise ProjectStateError("JSON 同步报告与事实对象不一致")
    if f"sync-fact-sha256:{fact_digest}" not in md_temp.read_text(encoding="utf-8"):
        raise ProjectStateError("Markdown 同步报告与事实对象不一致")
    # JSON 是成功提交标记，最后替换；缺少 JSON 时不得认定同步成功。
    md_temp.replace(md_path)
    json_temp.replace(json_path)
    return json_path, md_path


def controlled_sync(
    state: dict[str, Any],
    installed_baseline: dict[str, str],
    backup_root: str | Path,
    *,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    errors = []
    if project_root is None:
        errors.append("受控同步必须提供独立项目根目录和同步报告位置")
    errors.extend(verify_sync_gate(state, installed_baseline, project_root))
    if errors:
        raise ProjectStateError("; ".join(errors))
    exclusions = state["sync"]["exclusions"]
    working = _target(state, "working_repository")
    installed = _target(state, "installed_repository")
    backup = Path(backup_root).resolve()
    copied: list[str] = []
    before = repository_manifest(installed, exclusions)
    started_at = datetime.now(timezone.utc).isoformat()
    expected_initial = repository_manifest(working, exclusions)
    if before == expected_initial:
        validation = validate_installed_repository(working, installed, exclusions)
        if not validation.success:
            raise SyncTransactionError(
                "无变化同步的安装副本验证失败",
                {
                    "result": "FAIL",
                    "no_changes": True,
                    "installation_validation": validation.as_dict(),
                },
                _failed_project_state(state, RollbackResult(True)),
            )
        result = {
            "result": "PASS",
            "no_changes": True,
            "project_id": state.get("project_id"),
            "project_root": str(Path(project_root).resolve()),
            "source_repository": str(working),
            "destination_repository": str(installed),
            "final_evaluation": state.get("last_evaluation"),
            "pre_sync_diff": [],
            "unknown_install_changes": [],
            "backup": None,
            "expected_files": sorted(expected_initial),
            "copied": [],
            "unchanged": sorted(expected_initial),
            "excluded": list(exclusions),
            "failed_files": [],
            "rollback_triggered": False,
            "rollback_result": None,
            "post_sync_diff": [],
            "installation_validation": validation.as_dict(),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reason": "no_changes",
            "blocker": None,
        }
        json_report, md_report = write_sync_report(project_root, result)
        result["reports"] = [str(json_report), str(md_report)]
        return result
    try:
        snapshot = snapshot_repository(installed, backup, exclusions)
    except SnapshotError as exc:
        rollback = RollbackResult(True)
        result = {
            "result": "FAIL", "stage": "snapshot", "reason": str(exc),
            "rollback_triggered": False, "rollback": rollback,
            "before": before, "started_at": started_at,
        }
        if project_root is not None:
            report_payload = dict(result)
            report_payload["rollback"] = rollback.as_dict()
            try:
                reports = write_sync_report(project_root, report_payload)
                result["reports"] = [str(item) for item in reports]
            except (OSError, ProjectStateError) as report_exc:
                result["report_error"] = str(report_exc)
        updated_state = _failed_project_state(state, rollback)
        project_yaml = Path(project_root) / "project.yaml" if project_root else None
        if not rollback.success and project_yaml is not None and project_yaml.is_file():
            updated_state = persist_sync_failure_state(
                project_yaml, state, rollback
            )
        raise SyncTransactionError(str(exc), result, updated_state) from exc
    staged = Path(tempfile.mkdtemp(prefix="skill-sync-", dir=backup.parent))
    try:
        expected = expected_initial
        for relative in sorted(expected):
            staged_file = staged / relative
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(working / relative, staged_file)
        if repository_manifest(staged, []) != expected:
            raise OSError("暂存区哈希校验失败")
        for relative in sorted(set(before) - set(expected)):
            _controlled_install_destination(state, installed / relative).unlink()
        for relative in sorted(expected):
            destination = _controlled_install_destination(state, installed / relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged / relative, destination)
            copied.append(relative)
        after = repository_manifest(installed, exclusions)
        if after != expected:
            raise OSError("安装副本提交后哈希校验失败")
        installation_validation = validate_installed_repository(
            working, installed, exclusions
        )
        if not installation_validation.success:
            raise OSError("安装副本完整性验证失败")
        result = {
            "result": "PASS", "no_changes": False,
            "project_id": state.get("project_id"),
            "project_root": str(Path(project_root).resolve()),
            "source_repository": str(working),
            "destination_repository": str(installed),
            "final_evaluation": state.get("last_evaluation"),
            "pre_sync_diff": sorted(set(expected) ^ set(before)) + sorted(
                key for key in set(expected) & set(before)
                if expected[key] != before[key]
            ),
            "unknown_install_changes": [],
            "backup": snapshot,
            "expected_files": sorted(expected),
            "copied": copied,
            "unchanged": sorted(
                key for key in set(expected) & set(before)
                if expected[key] == before[key]
            ),
            "excluded": list(exclusions),
            "failed_files": [],
            "rollback_result": None,
            "post_sync_diff": [],
            "installation_validation": installation_validation.as_dict(),
            "rollback_triggered": False, "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "reason": None,
            "blocker": None,
        }
        if project_root is not None:
            json_report, md_report = write_sync_report(project_root, result)
            result["reports"] = [str(json_report), str(md_report)]
    except (OSError, ProjectStateError) as exc:
        rollback = restore_snapshot(installed, snapshot, exclusions)
        result = {
            "result": "FAIL" if rollback.success else "BLOCKED",
            "stage": "commit", "reason": str(exc), "rollback_triggered": True,
            "rollback": rollback, "before": before,
            "after": repository_manifest(installed, exclusions),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        if project_root is not None:
            report_payload = dict(result)
            report_payload["rollback"] = rollback.as_dict()
            try:
                reports = write_sync_report(project_root, report_payload)
                result["reports"] = [str(item) for item in reports]
            except (OSError, ProjectStateError) as report_exc:
                result["report_error"] = str(report_exc)
        updated_state = _failed_project_state(state, rollback)
        project_yaml = Path(project_root) / "project.yaml" if project_root else None
        if not rollback.success and project_yaml is not None and project_yaml.is_file():
            updated_state = persist_sync_failure_state(
                project_yaml, state, rollback
            )
        raise SyncTransactionError(str(exc), result, updated_state) from exc
    finally:
        shutil.rmtree(staged, ignore_errors=True)
    return result


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="Skill Maintenance 受控维护工具")
    parser.add_argument(
        "action",
        choices=("check-symlink-privilege", "block-symlink", "resume-symlink-block"),
    )
    parser.add_argument("project_yaml", nargs="?", type=Path)
    args = parser.parse_args()
    if args.action == "check-symlink-privilege":
        capability = probe_windows_symlink_capability()
        print(json.dumps(capability, ensure_ascii=False, indent=2))
        return 0 if capability["available"] else 2
    if args.project_yaml is None:
        parser.error("该操作必须提供 project.yaml")
    if args.action == "block-symlink":
        persist_symlink_environment_block(args.project_yaml)
        print("BLOCKED: windows_symlink_privilege_missing")
        return 0
    try:
        updated = persist_symlink_environment_resume(args.project_yaml)
    except ProjectStateError as exc:
        print(f"BLOCKED: {exc}")
        return 2
    print(f"RESUMED: {updated['status']}/{updated['next_role']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
