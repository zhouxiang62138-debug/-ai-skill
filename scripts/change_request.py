"""已完成项目的 Change Request 确定性工作流。

本模块只负责文件协议、Schema 约束、状态转换和角色门禁，不替代 Planner、
Generator 或 Evaluator 的专业判断。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from runtime.event_types import ActorType, EventType
from runtime.attestation import attestation_hash, required_steps_hash
from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.verifiers import RuntimeVerifierRegistry

from project_state import (
    ProjectStateError,
    load_project_state,
    parse_project_yaml,
    serialize_project_state,
    validate_project_state,
    write_project_state_atomic,
)


CR_ID_RE = re.compile(r"^CR-(\d{4})$")
ITEM_ID_RE = re.compile(r"^(CR-\d{4})-(\d{2})$")
EVENT_ID_RE = re.compile(r"^event-(\d{3})$")
APPROVAL_ID_RE = re.compile(r"^approval-(\d{3})$")
EVALUATION_ID_RE = re.compile(r"^evaluation-(\d{3})$")
HANDOFF_ID_RE = re.compile(r"^handoff-(\d{3})$")
ROLLBACK_ID_RE = re.compile(r"^rollback-(\d{3})$")
REOPENABLE_STATUSES = {"ACCEPTED", "ARCHIVED"}
ACTIVE_PROJECT_STATUSES = {
    "CHANGE_REQUESTED",
    "WAITING_FOR_CHANGE_APPROVAL",
    "IMPLEMENTING",
    "EVALUATING",
    "RELEASE_READY",
}
CHANGE_TYPES = {
    "bug_fix",
    "ui_improvement",
    "usability_improvement",
    "content_change",
    "configuration_change",
    "new_feature",
    "behavior_change",
    "performance_improvement",
    "security_change",
    "compatibility_change",
    "data_migration",
    "scope_change",
    "major_change",
}
CHANGE_STATUSES = {
    "PROPOSED",
    "ANALYZING",
    "WAITING_FOR_APPROVAL",
    "APPROVED",
    "IMPLEMENTING",
    "EVALUATING",
    "RELEASE_READY",
    "WAITING_FOR_USER",
    "ACCEPTED",
    "REJECTED",
    "CANCELLED",
    "BLOCKED",
}
EVENT_TRANSITIONS = {
    None: {"PROPOSED"},
    "PROPOSED": {"ANALYZING", "CANCELLED", "BLOCKED"},
    "ANALYZING": {"WAITING_FOR_APPROVAL", "BLOCKED"},
    "WAITING_FOR_APPROVAL": {
        "APPROVED",
        "REJECTED",
        "CANCELLED",
        "ANALYZING",
        "BLOCKED",
    },
    "APPROVED": {"IMPLEMENTING", "CANCELLED", "BLOCKED"},
    "IMPLEMENTING": {"EVALUATING", "BLOCKED"},
    "EVALUATING": {
        "IMPLEMENTING",
        "ANALYZING",
        "RELEASE_READY",
        "WAITING_FOR_USER",
        "BLOCKED",
    },
    "RELEASE_READY": {"ACCEPTED", "BLOCKED"},
    "WAITING_FOR_USER": {"IMPLEMENTING", "CANCELLED"},
    "BLOCKED": {"ANALYZING", "IMPLEMENTING", "EVALUATING", "CANCELLED"},
    "REJECTED": set(),
    "CANCELLED": set(),
    "ACCEPTED": set(),
}
REQUEST_KEYS = {
    "schema_version",
    "change_request_id",
    "project_id",
    "created_at",
    "created_by",
    "source",
    "raw_feedback",
    "baseline",
    "requested_changes",
}
ITEM_KEYS = {"change_item_id", "type", "description", "approval_status"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _safe_relative_path(root: Path, reference: str, *, must_exist: bool = False) -> Path:
    if not _nonempty(reference):
        raise ProjectStateError("项目内路径不能为空")
    pure = Path(reference.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts:
        raise ProjectStateError(f"项目内路径非法：{reference}")
    candidate = (root / pure).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectStateError(f"项目内路径逃逸：{reference}") from exc
    if must_exist and not candidate.exists():
        raise ProjectStateError(f"项目内路径不存在：{reference}")
    return candidate


def _write_new_yaml(path: Path, value: dict[str, Any]) -> None:
    """追加式写入；目标存在时拒绝，不删除任何历史工件。"""

    if path.exists():
        raise ProjectStateError(f"追加式工件已存在，禁止覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(serialize_project_state(value), encoding="utf-8")
    if path.exists():
        raise ProjectStateError(f"追加式工件并发冲突：{path}")
    temp.replace(path)


def _request_fingerprint(
    project_id: str,
    base_revision: int,
    raw_feedback: str,
    requested_changes: list[dict[str, Any]],
) -> str:
    canonical_changes = [
        {
            key: value
            for key, value in item.items()
            if key not in {"change_item_id", "approval_status"}
        }
        for item in requested_changes
    ]
    payload = {
        "project_id": project_id,
        "base_revision": base_revision,
        "raw_feedback": raw_feedback,
        "requested_changes": canonical_changes,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _staged_request_path(root: Path, fingerprint: str) -> Path:
    return root / "change_requests" / ".staging" / f"create-{fingerprint}.yaml"


def _runtime_orchestrator(
    root: Path,
    state: dict[str, Any],
    runtime: Any | None,
    control_plane_home: str | Path | None,
) -> Any:
    if state.get("schema_version") != 7:
        return runtime
    if runtime is None:
        from runtime.orchestrator import Orchestrator

        runtime = Orchestrator(root, control_plane_home=control_plane_home)
    if Path(runtime.root).resolve() != root:
        raise ProjectStateError("Change Request Runtime 与项目根目录不一致")
    return runtime


def _commit_v7_role_step(
    root: Path,
    runtime: Any,
    *,
    role: str,
    source_status: str,
    target_status: str,
    changed_fields: dict[str, Any],
    expected_revision: int,
    idempotency_key: str,
    worker_id: str,
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用已有 Role Run 或新建一次 Role Run，经 F10 CAS 提交生命周期步骤。"""

    if run_context is None:
        run_context = runtime.start(worker_id=worker_id)
    selection = run_context.get("selection")
    if (
        selection is None
        or getattr(selection, "kind", None) != "ROLE"
        or getattr(selection, "target", None) != role
        or not run_context.get("run_id")
        or not run_context.get("lease_token")
    ):
        raise ProjectStateError(f"Runtime 未进入 {role} Role Run")
    session_id = str(run_context["session_id"])
    run_id = str(run_context["run_id"])
    lease_token = str(run_context["lease_token"])
    transition = {
        "source_status": source_status,
        "target_status": target_status,
        "changed_fields": changed_fields,
        "expected_revision": expected_revision,
        "idempotency_key": idempotency_key,
    }
    verifier = RuntimeVerifierRegistry(root).verify_transition(
        role,
        source_status,
        target_status,
        changed_fields,
        expected_revision,
    )
    if verifier.get("passed") is not True:
        raise ProjectStateError("Change Request Runtime transition verifier 失败：" + str(verifier.get("details", "")))
    store = runtime.store
    context = ContextBuilder(store).build(ContextBuildRequest(session_id, run_id, role))
    invocation = store.create_model_invocation(
        session_id,
        run_id,
        role,
        context.context_id,
        idempotency_key=f"change-request-transition-invocation:{idempotency_key}",
    )
    state = load_project_state(root / "project.yaml")
    attestation = store.create_phase_attestation(
        session_id=session_id,
        run_id=run_id,
        role=role,
        project_revision=int(state["runtime"]["revision"]),
        context_id=context.context_id,
        invocation_id=str(invocation["invocation_id"]),
        required_steps_hash=required_steps_hash(("runtime_transition",)),
        verifier_results={"runtime_transition": verifier},
        idempotency_key=f"change-request-transition-attestation:{idempotency_key}",
    )
    prepared = dict(transition)
    prepared["attestation_id"] = attestation["attestation_id"]
    try:
        committed = runtime.commit_step(session_id, run_id, lease_token, prepared)
        store.complete_model_invocation(
            session_id,
            str(invocation["invocation_id"]),
            result_hash=attestation_hash(committed),
        )
        return committed
    except Exception as exc:
        store.complete_model_invocation(
            session_id,
            str(invocation["invocation_id"]),
            status="FAILED",
            result_hash=attestation_hash({"error": str(exc)[:200]}),
        )
        raise


def _commit_v7_role_state(
    root: Path,
    state: dict[str, Any],
    runtime: Any,
    *,
    role: str,
    next_state: dict[str, Any],
    expected_revision: int,
    idempotency_key: str,
    worker_id: str,
) -> dict[str, Any]:
    """用 F10 CAS 提交不改变 workflow status 的角色业务字段。"""

    return runtime.commit_role_state(
        str(state["runtime"]["session_id"]),
        role,
        next_state,
        project_yaml=root / "project.yaml",
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        worker_id=worker_id,
    )


def _commit_v7_role_transition(
    root: Path,
    state: dict[str, Any],
    runtime: Any,
    *,
    role: str,
    source_status: str,
    target_status: str,
    changed_fields: dict[str, Any],
    expected_revision: int,
    idempotency_key: str,
    worker_id: str,
) -> dict[str, Any]:
    """在没有活动 Role Run 的等待态入口上，仍只经 F10 CAS 迁移。"""

    return runtime.commit_role_transition(
        str(state["runtime"]["session_id"]),
        role,
        {
            "project_yaml": root / "project.yaml",
            "source_status": source_status,
            "target_status": target_status,
            "changed_fields": changed_fields,
            "expected_revision": expected_revision,
            "idempotency_key": idempotency_key,
        },
        worker_id=worker_id,
    )


def _append_v7_stage_audit(
    root: Path,
    runtime: Any,
    *,
    change_request_id: str,
    stage: str,
    actor: str,
    artifact: str | None = None,
) -> None:
    """把 Change Request 阶段写入 F10，payload 只包含引用与状态元数据。"""

    state = load_project_state(root / "project.yaml")
    session_id = str(state["runtime"]["session_id"])
    revision = int(state["runtime"]["revision"])
    key = f"change-request-stage:{change_request_id}:{stage}:{revision}"
    if any(event.idempotency_key == key for event in runtime.store.list_events(session_id)):
        return
    actor_type = ActorType.USER if actor == "user" else ActorType.ROLE
    runtime.store.append_event(
        session_id,
        EventType.CHANGE_REQUEST_STAGE,
        actor_type,
        actor,
        idempotency_key=key,
        correlation_id=f"change-request:{change_request_id}",
        payload={
            "change_request_id": change_request_id,
            "stage": stage,
            "actor": actor,
            "status": state["status"],
            "revision": revision,
            "artifact": artifact,
        },
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = parse_project_yaml(path.read_text(encoding="utf-8"))
    except (OSError, ProjectStateError) as exc:
        raise ProjectStateError(f"无法读取结构化工件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise ProjectStateError(f"结构化工件根节点必须是对象：{path}")
    return value


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _next_identifier(directory: Path, pattern: re.Pattern[str], prefix: str, width: int) -> str:
    maximum = 0
    if directory.is_dir():
        for path in directory.iterdir():
            match = pattern.fullmatch(path.stem if path.is_file() else path.name)
            if match:
                maximum = max(maximum, int(match.group(1)))
    return f"{prefix}{maximum + 1:0{width}d}"


def classify_change(description: str) -> str:
    """使用保守且可审计的关键词优先级做初步分类。"""

    text = description.casefold()
    rules = (
        ("major_change", ("企业级", "重做", "完全改成", "replace product", "major")),
        ("security_change", ("安全", "权限", "认证", "加密", "security", "auth")),
        ("data_migration", ("迁移数据", "数据迁移", "schema migration", "migrate data")),
        ("performance_improvement", ("性能", "速度", "延迟", "performance", "latency")),
        ("compatibility_change", ("兼容", "适配", "compatibility", "support version")),
        ("bug_fix", ("修复", "错误", "不更新", "崩溃", "bug", "fix", "broken")),
        ("new_feature", ("增加", "新增", "支持导出", "add ", "new feature", "export")),
        ("ui_improvement", ("布局", "按钮", "颜色", "首页", "ui", "layout")),
        ("content_change", ("文案", "文字", "内容", "copy", "content")),
        ("configuration_change", ("配置", "config", "setting")),
        ("usability_improvement", ("易用", "操作", "简化", "usability")),
        ("behavior_change", ("行为", "逻辑", "behavior")),
        ("scope_change", ("范围", "scope")),
    )
    for change_type, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return change_type
    return "behavior_change"


def _request_path(root: Path, change_request_id: str) -> Path:
    if not CR_ID_RE.fullmatch(change_request_id):
        raise ProjectStateError("Change Request ID 格式无效")
    return root / "change_requests" / f"{change_request_id}.yaml"


def validate_change_request(
    record: dict[str, Any],
    *,
    filename: str | None = None,
    project_id: str | None = None,
    project_root: str | Path | None = None,
) -> list[str]:
    errors: list[str] = []
    unknown = set(record) - REQUEST_KEYS
    missing = REQUEST_KEYS - set(record)
    if unknown:
        errors.append("存在未知关键字段：" + ", ".join(sorted(unknown)))
    if missing:
        errors.append("缺少字段：" + ", ".join(sorted(missing)))
    cr_id = record.get("change_request_id")
    if not isinstance(cr_id, str) or not CR_ID_RE.fullmatch(cr_id):
        errors.append("change_request_id 必须符合 CR-0001")
    if filename and isinstance(cr_id, str) and filename != f"{cr_id}.yaml":
        errors.append("Change Request ID 与文件名不一致")
    if project_id is not None and record.get("project_id") != project_id:
        errors.append("project_id 与目标项目不一致")
    if record.get("schema_version") != "1.0":
        errors.append("schema_version 必须是 1.0")
    if record.get("created_by") not in {"user", "external", "system"}:
        errors.append("created_by 无效")
    if not _validate_timestamp(record.get("created_at")):
        errors.append("created_at 必须是带时区的 ISO 时间")
    if not _nonempty(record.get("raw_feedback")):
        errors.append("raw_feedback 不能为空")
    source = record.get("source")
    if not isinstance(source, dict) or set(source) != {"type", "description"}:
        errors.append("source 字段结构无效")
    elif source.get("type") not in {
        "user_feedback",
        "external_feedback",
        "production_incident",
        "follow_up",
    } or not _nonempty(source.get("description")):
        errors.append("source 内容无效")
    baseline = record.get("baseline")
    baseline_keys = {
        "previous_project_status",
        "plan_version",
        "release_version",
        "last_evaluation",
        "commit_sha",
    }
    if not isinstance(baseline, dict) or set(baseline) != baseline_keys:
        errors.append("baseline 字段结构无效")
    elif baseline.get("previous_project_status") not in REOPENABLE_STATUSES:
        errors.append("baseline.previous_project_status 无效")
    changes = record.get("requested_changes")
    if not isinstance(changes, list) or not changes:
        errors.append("requested_changes 至少需要一项")
    else:
        seen: set[str] = set()
        for index, item in enumerate(changes, 1):
            if not isinstance(item, dict) or set(item) != ITEM_KEYS:
                errors.append(f"requested_changes[{index}] 字段结构无效")
                continue
            item_id = item.get("change_item_id")
            if not isinstance(item_id, str) or not ITEM_ID_RE.fullmatch(item_id):
                errors.append(f"requested_changes[{index}].change_item_id 无效")
            elif not isinstance(cr_id, str) or not item_id.startswith(f"{cr_id}-"):
                errors.append(f"requested_changes[{index}] ID 不属于当前请求")
            elif item_id in seen:
                errors.append("Change Item ID 不允许重复")
            else:
                seen.add(item_id)
            if item.get("type") not in CHANGE_TYPES:
                errors.append(f"requested_changes[{index}].type 无效")
            if not _nonempty(item.get("description")):
                errors.append(f"requested_changes[{index}].description 不能为空")
            if item.get("approval_status") not in {"PENDING", "APPROVED", "REJECTED"}:
                errors.append(f"requested_changes[{index}].approval_status 无效")
    if project_root is not None:
        root = Path(project_root).resolve()
        for field in ("last_evaluation",):
            reference = (baseline or {}).get(field) if isinstance(baseline, dict) else None
            if reference:
                try:
                    _safe_relative_path(root, reference, must_exist=True)
                except ProjectStateError as exc:
                    errors.append(str(exc))
    return errors


def load_change_request(
    project_root: str | Path, change_request_id: str
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    path = _request_path(root, change_request_id)
    record = _load_yaml(path)
    state = load_project_state(root / "project.yaml")
    errors = validate_change_request(
        record,
        filename=path.name,
        project_id=state["project_id"],
        project_root=root,
    )
    if errors:
        raise ProjectStateError("Change Request 校验失败：" + "; ".join(errors))
    return record


def _event_paths(root: Path, change_request_id: str) -> list[Path]:
    directory = root / "change_requests" / change_request_id / "events"
    return sorted(directory.glob("event-*.yaml")) if directory.is_dir() else []


def load_events(project_root: str | Path, change_request_id: str) -> list[dict[str, Any]]:
    root = Path(project_root).resolve()
    events = [_load_yaml(path) for path in _event_paths(root, change_request_id)]
    previous: str | None = None
    for index, event in enumerate(events, 1):
        expected_id = f"event-{index:03d}"
        if event.get("event_id") != expected_id:
            raise ProjectStateError("Change Request 事件序号不连续")
        if event.get("change_request_id") != change_request_id:
            raise ProjectStateError("Change Request 事件关联错误")
        if event.get("from_status") != previous:
            raise ProjectStateError("Change Request 事件状态链不连续")
        target = event.get("to_status")
        if target not in EVENT_TRANSITIONS.get(previous, set()):
            raise ProjectStateError(f"非法 Change Request 状态转换：{previous} -> {target}")
        if not _validate_timestamp(event.get("created_at")):
            raise ProjectStateError("Change Request 事件时间无效")
        previous = target
    return events


def current_change_status(project_root: str | Path, change_request_id: str) -> str:
    events = load_events(project_root, change_request_id)
    if not events:
        raise ProjectStateError("Change Request 缺少生命周期事件")
    return str(events[-1]["to_status"])


def append_event(
    project_root: str | Path,
    change_request_id: str,
    to_status: str,
    *,
    actor: str,
    reason: str,
    artifact: str | None = None,
) -> Path:
    root = Path(project_root).resolve()
    load_change_request(root, change_request_id)
    events = load_events(root, change_request_id)
    previous = events[-1]["to_status"] if events else None
    if to_status not in EVENT_TRANSITIONS.get(previous, set()):
        raise ProjectStateError(f"非法 Change Request 状态转换：{previous} -> {to_status}")
    if actor not in {"change_request", "planner", "generator", "evaluator", "user"}:
        raise ProjectStateError("事件 actor 无效")
    if not _nonempty(reason):
        raise ProjectStateError("事件 reason 不能为空")
    if artifact:
        _safe_relative_path(root, artifact, must_exist=True)
    event_id = f"event-{len(events) + 1:03d}"
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "change_request_id": change_request_id,
        "from_status": previous,
        "to_status": to_status,
        "created_at": _now(),
        "actor": actor,
        "reason": reason,
        "artifact": artifact,
    }
    path = root / "change_requests" / change_request_id / "events" / f"{event_id}.yaml"
    _write_new_yaml(path, event)
    return path


def _normalize_changes(
    change_request_id: str, changes: Iterable[str | dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, value in enumerate(changes, 1):
        if isinstance(value, str):
            description = value
            change_type = classify_change(value)
        elif isinstance(value, dict):
            description = value.get("description")
            change_type = value.get("type") or classify_change(str(description or ""))
        else:
            raise ProjectStateError("每个修改项必须是字符串或对象")
        if not _nonempty(description):
            raise ProjectStateError("修改项描述不能为空")
        if change_type not in CHANGE_TYPES:
            raise ProjectStateError(f"非法 Change Type：{change_type}")
        result.append(
            {
                "change_item_id": f"{change_request_id}-{index:02d}",
                "type": change_type,
                "description": description.strip(),
                "approval_status": "PENDING",
            }
        )
    if not result:
        raise ProjectStateError("至少需要一个修改项")
    return result


def migrate_completed_project_for_change_request(
    project_root: str | Path,
) -> dict[str, Any]:
    """首次变更时把 v3/v4/v5 完成项目最小迁移到 v6，并保留原文件。"""

    root = Path(project_root).resolve()
    project_yaml = root / "project.yaml"
    state = load_project_state(project_yaml)
    source_version = state.get("schema_version")
    if source_version == 6:
        return {"result": "PASS", "changed": False, "reason": "already_v6"}
    if source_version not in {3, 4, 5}:
        raise ProjectStateError("只有 v3/v4/v5 完成项目可执行 Change Request 迁移")
    if state.get("status") not in REOPENABLE_STATUSES:
        raise ProjectStateError("只允许迁移 ACCEPTED/ARCHIVED 的已完成项目")
    errors = validate_project_state(state, root)
    if errors:
        raise ProjectStateError("迁移前项目状态无效：" + "; ".join(errors))
    directory = root / "memory" / "migrations"
    directory.mkdir(parents=True, exist_ok=True)
    number = len(list(directory.glob("change-request-migration-*.yaml"))) + 1
    migration_id = f"change-request-migration-{number:03d}"
    backup = directory / f"{migration_id}-project-v{source_version}.yaml"
    record_path = directory / f"{migration_id}.yaml"
    _write_new_text(backup, project_yaml.read_text(encoding="utf-8"))
    migrated = copy.deepcopy(state)
    migrated["schema_version"] = 6
    migrated.setdefault("project_type", "application")
    migrated.setdefault("product_spec_status", "not_started")
    migrated.setdefault("plan_status", "not_started")
    migrated.setdefault("plan_approval_status", "not_requested")
    migrated.setdefault("exploration_trigger_reasons", [])
    migrated.setdefault("exploration_generation_attempt", 0)
    migrated.setdefault("design_feedback_status", "not_started")
    migrated.setdefault("design_feedback_round", 0)
    migrated["iteration_sequence"] = max(
        1, int(migrated.get("iteration_sequence") or 1)
    )
    migrated["automatic_retry_allowed"] = False
    migrated.setdefault("last_issue_package", None)
    migrated.setdefault("last_generator_response", None)
    migrated.setdefault("evidence_manifest", None)
    migrated.setdefault("iteration_metrics", None)
    migrated.setdefault("retry_history", [])
    migrated.setdefault("routing_disagreements", [])
    migrated.setdefault("escalation_record", None)
    migrated.setdefault("decision_summary_record", None)
    migrated.setdefault("active_change_request", None)
    migrated.setdefault("change_cycle", 0)
    migrated.setdefault("change_context", None)
    migrated.setdefault("release_version", None)
    migrated.setdefault("current_release", None)
    record = {
        "schema_version": "1.0",
        "migration_id": migration_id,
        "created_at": _now(),
        "from_schema_version": source_version,
        "to_schema_version": 6,
        "reason": "首次创建已完成项目 Change Request 的最小兼容迁移",
        "backup": backup.relative_to(root).as_posix(),
        "historical_release_fabricated": False,
        "status": "MIGRATED",
    }
    _write_new_yaml(record_path, record)
    migrated["schema_migration_record"] = record_path.relative_to(root).as_posix()
    errors = validate_project_state(migrated, root)
    if errors:
        raise ProjectStateError("Change Request 迁移预览无效：" + "; ".join(errors))
    write_project_state_atomic(project_yaml, migrated)
    return {
        "result": "PASS",
        "changed": True,
        "backup": str(backup),
        "migration_record": str(record_path),
    }


def _prepare_v7_change_request(
    root: Path,
    state: dict[str, Any],
    *,
    raw_feedback: str,
    requested_changes: Iterable[str | dict[str, Any]],
    source_type: str,
    source_description: str,
    created_by: str,
    staged_path: Path,
) -> tuple[dict[str, Any], str]:
    requested_changes = list(requested_changes)
    normalized_changes = _normalize_changes(
        _next_identifier(root / "change_requests", CR_ID_RE, "CR-", 4),
        requested_changes,
    )
    base_revision = int((state.get("runtime") or {}).get("revision", 0))
    fingerprint = _request_fingerprint(
        str(state["project_id"]), base_revision, raw_feedback, normalized_changes
    )
    if staged_path.is_file():
        request = _load_yaml(staged_path)
        errors = validate_change_request(
            request, project_id=state["project_id"], project_root=root
        )
        if errors:
            raise ProjectStateError("staged Change Request 无效：" + "; ".join(errors))
        if (
            request["project_id"] != state["project_id"]
            or request["raw_feedback"] != raw_feedback
            or request["created_by"] != created_by
            or request["source"]
            != {"type": source_type, "description": source_description}
            or [
                (item["type"], item["description"])
                for item in request["requested_changes"]
            ]
            != [
                (item["type"], item["description"])
                for item in normalized_changes
            ]
        ):
            raise ProjectStateError("staged Change Request request mismatch")
        staged_fingerprint = staged_path.stem.removeprefix("create-")
        if not re.fullmatch(r"[0-9a-f]{64}", staged_fingerprint):
            raise ProjectStateError("staged Change Request fingerprint invalid")
        return request, staged_fingerprint
        expected = _request_fingerprint(
            str(state["project_id"]),
            base_revision,
            str(request["raw_feedback"]),
            list(request["requested_changes"]),
        )
        if expected != fingerprint:
            raise ProjectStateError("staged Change Request 与当前请求不一致")
        return request, fingerprint

    change_request_id = _next_identifier(
        root / "change_requests", CR_ID_RE, "CR-", 4
    )
    request = {
        "schema_version": "1.0",
        "change_request_id": change_request_id,
        "project_id": state["project_id"],
        "created_at": _now(),
        "created_by": created_by,
        "source": {"type": source_type, "description": source_description},
        "raw_feedback": raw_feedback,
        "baseline": {
            "previous_project_status": state["status"],
            "plan_version": state.get("plan_version"),
            "release_version": state.get("release_version")
            or state.get("current_release"),
            "last_evaluation": state.get("last_evaluation"),
            "commit_sha": _git_commit(root / "code")
            if (root / "code").is_dir()
            else _git_commit(root),
        },
        "requested_changes": normalized_changes,
    }
    errors = validate_change_request(
        request,
        filename=f"{change_request_id}.yaml",
        project_id=state["project_id"],
        project_root=root,
    )
    if errors:
        raise ProjectStateError("Change Request 创建数据无效：" + "; ".join(errors))
    _write_new_yaml(staged_path, request)
    return request, fingerprint


def _finalize_v7_change_request(
    root: Path,
    runtime: Any,
    request: dict[str, Any],
    *,
    base_revision: int,
    fingerprint: str,
    worker_id: str,
) -> dict[str, Any]:
    change_request_id = str(request["change_request_id"])
    request_path = _request_path(root, change_request_id)
    if not request_path.exists():
        _write_new_yaml(request_path, request)
    if not load_events(root, change_request_id):
        append_event(
            root,
            change_request_id,
            "PROPOSED",
            actor="change_request",
            reason="已通过 Runtime CAS 建立 Change Request 生命周期起点",
            artifact=request_path.relative_to(root).as_posix(),
        )

    state = load_project_state(root / "project.yaml")
    formal_reference = request_path.relative_to(root).as_posix()
    if state.get("change_request_record") != formal_reference:
        candidate = copy.deepcopy(state)
        candidate["change_request_record"] = formal_reference
        runtime.commit_module_state(
            str(state["runtime"]["session_id"]),
            "change_request",
            candidate,
            project_yaml=root / "project.yaml",
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-finalize:{fingerprint}",
            worker_id=worker_id,
        )
        state = load_project_state(root / "project.yaml")

    session_id = str(state["runtime"]["session_id"])
    audit_key = f"change-request-created:{fingerprint}"
    if not any(
        event.event_type == EventType.CHANGE_REQUEST_CREATED
        and event.idempotency_key == audit_key
        for event in runtime.store.list_events(session_id)
    ):
        runtime.store.append_event(
            session_id,
            EventType.CHANGE_REQUEST_CREATED,
            ActorType.MODULE,
            "change_request",
            idempotency_key=audit_key,
            correlation_id=f"change-request:{change_request_id}",
            payload={
                "change_request_id": change_request_id,
                "base_revision": base_revision,
                "new_revision": int(state["runtime"]["revision"]),
                "actor": "change_request",
            },
        )
    return {
        "result": "PASS",
        "change_request_id": change_request_id,
        "request_path": str(request_path),
        "project_status": state["status"],
        "next_role": state["next_role"],
        "classification": {
            change_type: sum(
                1
                for item in request["requested_changes"]
                if item["type"] == change_type
            )
            for change_type in sorted(
                {item["type"] for item in request["requested_changes"]}
            )
        },
    }


def _create_v7_change_request(
    root: Path,
    state: dict[str, Any],
    *,
    raw_feedback: str,
    requested_changes: Iterable[str | dict[str, Any]],
    source_type: str,
    source_description: str,
    created_by: str,
    runtime: Any | None,
    control_plane_home: str | Path | None,
    worker_id: str,
) -> dict[str, Any]:
    requested_changes = list(requested_changes)
    runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    if runtime is None:
        raise ProjectStateError("v7 Change Request 必须通过 Runtime CAS")
    if state.get("status") not in REOPENABLE_STATUSES and not state.get(
        "active_change_request"
    ):
        raise ProjectStateError("只有 ACCEPTED 或 ARCHIVED 项目可以创建活动 Change Request")

    base_revision = int((state.get("runtime") or {}).get("revision", 0))
    normalized_input = _normalize_changes(
        _next_identifier(root / "change_requests", CR_ID_RE, "CR-", 4),
        requested_changes,
    )
    fingerprint = _request_fingerprint(
        str(state["project_id"]),
        base_revision,
        raw_feedback,
        normalized_input,
    )
    staged_path = _staged_request_path(root, fingerprint)
    if state.get("active_change_request"):
        staging_dir = root / "change_requests" / ".staging"
        for candidate in sorted(staging_dir.glob("create-*.yaml")):
            try:
                staged_request = _load_yaml(candidate)
            except ProjectStateError:
                continue
            if staged_request.get("change_request_id") == state["active_change_request"]:
                staged_path = candidate
                break
    request, fingerprint = _prepare_v7_change_request(
        root,
        state,
        raw_feedback=raw_feedback,
        requested_changes=requested_changes,
        source_type=source_type,
        source_description=source_description,
        created_by=created_by,
        staged_path=staged_path,
    )
    if state.get("active_change_request"):
        if state["active_change_request"] != request["change_request_id"]:
            raise ProjectStateError("项目已有其他 active_change_request")
        return _finalize_v7_change_request(
            root,
            runtime,
            request,
            base_revision=base_revision,
            fingerprint=fingerprint,
            worker_id=worker_id,
        )

    change_context = {
        "previous_project_status": state["status"],
        "change_cycle": int(state.get("change_cycle") or 0) + 1,
        "evaluation_iteration": 0,
    }
    changed_fields = {
        "status": "CHANGE_REQUESTED",
        "next_role": "planner",
        "active_module": None,
        "active_change_request": request["change_request_id"],
        "change_context": change_context,
        "change_cycle": change_context["change_cycle"],
        "current_iteration": 0,
        "automatic_retry_allowed": True,
    }
    runtime.commit_module_step(
        str(state["runtime"]["session_id"]),
        "change_request",
        {
            "project_yaml": root / "project.yaml",
            "source_status": state["status"],
            "target_status": "CHANGE_REQUESTED",
            "changed_fields": changed_fields,
            "expected_revision": base_revision,
            "idempotency_key": f"change-request-open:{fingerprint}",
        },
        worker_id=worker_id,
    )
    return _finalize_v7_change_request(
        root,
        runtime,
        request,
        base_revision=base_revision,
        fingerprint=fingerprint,
        worker_id=worker_id,
    )


def create_change_request(
    project_root: str | Path,
    *,
    raw_feedback: str,
    requested_changes: Iterable[str | dict[str, Any]],
    source_type: str = "user_feedback",
    source_description: str = "用户在项目完成后提交修改意见",
    created_by: str = "user",
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "change-request-module",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    project_yaml = root / "project.yaml"
    if not project_yaml.is_file():
        raise ProjectStateError("目标项目根目录缺少唯一 project.yaml")
    state = load_project_state(project_yaml)
    if state.get("schema_version") in {3, 4, 5}:
        migrate_completed_project_for_change_request(root)
        state = load_project_state(project_yaml)
    if state.get("schema_version") == 7:
        state_errors = validate_project_state(state, root)
        if state_errors:
            raise ProjectStateError("invalid project state: " + "; ".join(state_errors))
        return _create_v7_change_request(
            root,
            state,
            raw_feedback=raw_feedback,
            requested_changes=requested_changes,
            source_type=source_type,
            source_description=source_description,
            created_by=created_by,
            runtime=runtime,
            control_plane_home=control_plane_home,
            worker_id=worker_id,
        )
    state_errors = validate_project_state(state, root)
    if state_errors:
        raise ProjectStateError("目标项目状态无效：" + "; ".join(state_errors))
    if state.get("status") not in REOPENABLE_STATUSES:
        raise ProjectStateError(
            "只有 ACCEPTED 或 ARCHIVED 项目可创建活动 Change Request"
        )
    if state.get("active_change_request"):
        raise ProjectStateError("项目已存在 active_change_request")
    if not _nonempty(raw_feedback):
        raise ProjectStateError("必须逐字保存非空原始反馈")
    directory = root / "change_requests"
    change_request_id = _next_identifier(directory, CR_ID_RE, "CR-", 4)
    request = {
        "schema_version": "1.0",
        "change_request_id": change_request_id,
        "project_id": state["project_id"],
        "created_at": _now(),
        "created_by": created_by,
        "source": {"type": source_type, "description": source_description},
        "raw_feedback": raw_feedback,
        "baseline": {
            "previous_project_status": state["status"],
            "plan_version": state.get("plan_version"),
            "release_version": state.get("release_version")
            or state.get("current_release"),
            "last_evaluation": state.get("last_evaluation"),
            "commit_sha": _git_commit(root / "code")
            if (root / "code").is_dir()
            else _git_commit(root),
        },
        "requested_changes": _normalize_changes(change_request_id, requested_changes),
    }
    errors = validate_change_request(
        request,
        filename=f"{change_request_id}.yaml",
        project_id=state["project_id"],
        project_root=root,
    )
    if errors:
        raise ProjectStateError("Change Request 创建数据无效：" + "; ".join(errors))
    request_path = _request_path(root, change_request_id)
    _write_new_yaml(request_path, request)
    append_event(
        root,
        change_request_id,
        "PROPOSED",
        actor="change_request",
        reason="已保存外部修改意见并建立稳定来源记录",
        artifact=request_path.relative_to(root).as_posix(),
    )
    updated = copy.deepcopy(state)
    updated["status"] = "CHANGE_REQUESTED"
    updated["next_role"] = "planner"
    updated["active_module"] = None
    updated["active_change_request"] = change_request_id
    updated["change_context"] = {
        "previous_project_status": state["status"],
        "change_cycle": int(state.get("change_cycle") or 0) + 1,
        "evaluation_iteration": 0,
    }
    updated["change_cycle"] = updated["change_context"]["change_cycle"]
    updated["current_iteration"] = 0
    updated["automatic_retry_allowed"] = True
    state_errors = validate_project_state(updated, root)
    if state_errors:
        append_event(
            root,
            change_request_id,
            "BLOCKED",
            actor="change_request",
            reason="project.yaml 新状态未通过校验：" + "; ".join(state_errors),
        )
        raise ProjectStateError("重开状态未通过校验：" + "; ".join(state_errors))
    write_project_state_atomic(project_yaml, updated)
    return {
        "result": "PASS",
        "change_request_id": change_request_id,
        "request_path": str(request_path),
        "project_status": updated["status"],
        "next_role": updated["next_role"],
        "classification": {
            change_type: sum(
                1 for item in request["requested_changes"] if item["type"] == change_type
            )
            for change_type in sorted(
                {item["type"] for item in request["requested_changes"]}
            )
        },
    }


def cancel_change_request(
    project_root: str | Path,
    change_request_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("只能取消当前 active_change_request")
    status = current_change_status(root, change_request_id)
    if "CANCELLED" not in EVENT_TRANSITIONS.get(status, set()):
        raise ProjectStateError(f"当前请求状态 {status} 不允许取消")
    previous = (state.get("change_context") or {}).get("previous_project_status")
    if previous not in REOPENABLE_STATUSES:
        raise ProjectStateError("缺少可恢复的 previous_project_status")
    event_path = append_event(
        root,
        change_request_id,
        "CANCELLED",
        actor="user",
        reason=reason,
    )
    updated = copy.deepcopy(state)
    updated["status"] = previous
    updated["next_role"] = None
    updated["active_change_request"] = None
    updated["change_context"] = None
    updated["current_iteration"] = 0
    updated["automatic_retry_allowed"] = False
    errors = validate_project_state(updated, root)
    if errors:
        raise ProjectStateError("取消后的项目状态无效：" + "; ".join(errors))
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "restored_project_status": previous,
        "event_path": str(event_path),
    }


def recover_change_request_state(project_root: str | Path) -> dict[str, Any]:
    """只读核对项目状态与请求事件，给出确定性恢复建议。"""

    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    active = state.get("active_change_request")
    if not active:
        if state.get("status") in ACTIVE_PROJECT_STATUSES:
            return {
                "result": "BLOCKED",
                "reason": "活动变更项目状态缺少 active_change_request",
            }
        return {"result": "PASS", "reason": "no_active_change_request"}
    try:
        request_status = current_change_status(root, active)
    except ProjectStateError as exc:
        return {"result": "BLOCKED", "reason": str(exc)}
    expected = {
        "PROPOSED": "CHANGE_REQUESTED",
        "ANALYZING": "CHANGE_REQUESTED",
        "WAITING_FOR_APPROVAL": "WAITING_FOR_CHANGE_APPROVAL",
        "APPROVED": "WAITING_FOR_CHANGE_APPROVAL",
        "IMPLEMENTING": "IMPLEMENTING",
        "EVALUATING": "EVALUATING",
        "RELEASE_READY": "RELEASE_READY",
        "WAITING_FOR_USER": "WAITING_FOR_USER",
        "BLOCKED": "BLOCKED",
    }.get(request_status)
    if expected is None:
        return {
            "result": "BLOCKED",
            "reason": f"终态请求 {request_status} 不应保持 active",
        }
    if state.get("status") != expected:
        return {
            "result": "BLOCKED",
            "reason": "project.yaml 与 Change Request 生命周期不一致",
            "expected_project_status": expected,
            "actual_project_status": state.get("status"),
        }
    return {"result": "PASS", "request_status": request_status}


def _write_new_text(path: Path, text: str) -> None:
    if path.exists():
        raise ProjectStateError(f"追加式工件已存在，禁止覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    if path.exists():
        raise ProjectStateError(f"追加式工件并发冲突：{path}")
    temp.replace(path)


def _default_item_impact(item: dict[str, Any]) -> dict[str, Any]:
    change_type = item["type"]
    new_requirement = None
    requirement_action = "MODIFY"
    if change_type == "new_feature":
        suffix = item["change_item_id"].split("-")[-1]
        new_requirement = {
            "requirement_id": f"REQ-CHANGE-{suffix}",
            "description": item["description"],
            "acceptance_criteria": [
                {
                    "acceptance_criterion_id": f"AC-CHANGE-{suffix}-01",
                    "description": f"已实现并可验证：{item['description']}",
                }
            ],
        }
        requirement_action = "ADD"
    elif change_type == "major_change":
        requirement_action = "MAJOR_REPLAN"
    return {
        "change_item_id": item["change_item_id"],
        "classification": change_type,
        "requirement_action": requirement_action,
        "affected_requirements": [],
        "new_requirement": new_requirement,
        "affected_acceptance_criteria": [],
        "affected_components": [],
        "data_model_impact": change_type == "data_migration",
        "api_impact": change_type
        in {"new_feature", "behavior_change", "compatibility_change", "major_change"},
        "compatibility_impact": change_type
        in {"compatibility_change", "major_change", "data_migration"},
        "new_dependency_required": False,
        "regression_areas": [],
        "implementation_recommendation": "按批准范围实施并执行针对性与回归验证",
    }


def _validate_impact_analysis(
    analysis: dict[str, Any], request: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version",
        "change_request_id",
        "created_at",
        "risk_level",
        "scope_change",
        "database_migration_required",
        "new_dependencies_required",
        "backward_compatibility_risk",
        "items",
        "affected_components",
        "testing_impact",
        "approval_questions",
    }
    if set(analysis) != required:
        errors.append("Impact Analysis 字段集合无效")
    if analysis.get("change_request_id") != request.get("change_request_id"):
        errors.append("Impact Analysis 关联的 Change Request 不一致")
    if analysis.get("risk_level") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        errors.append("risk_level 无效")
    if analysis.get("backward_compatibility_risk") not in {
        "NONE",
        "LOW",
        "MEDIUM",
        "HIGH",
    }:
        errors.append("backward_compatibility_risk 无效")
    request_items = {
        item["change_item_id"]: item for item in request.get("requested_changes", [])
    }
    analysis_items = analysis.get("items")
    if not isinstance(analysis_items, list) or len(analysis_items) != len(request_items):
        errors.append("Impact Analysis 必须逐项覆盖所有 Change Item")
        return errors
    seen: set[str] = set()
    for item in analysis_items:
        if not isinstance(item, dict):
            errors.append("Impact Analysis Item 必须是对象")
            continue
        item_id = item.get("change_item_id")
        if item_id in seen:
            errors.append("Impact Analysis Change Item 不允许重复")
            continue
        seen.add(item_id)
        source = request_items.get(item_id)
        if source is None:
            errors.append(f"Impact Analysis 引用了未知 Change Item：{item_id}")
            continue
        if item.get("classification") != source["type"]:
            errors.append(f"{item_id} 分类与原请求不一致")
        if source["type"] == "new_feature":
            requirement = item.get("new_requirement")
            if not isinstance(requirement, dict) or not _nonempty(
                requirement.get("requirement_id")
            ):
                errors.append(f"{item_id} 新功能必须新增 Requirement")
            elif not requirement.get("acceptance_criteria"):
                errors.append(f"{item_id} 新功能必须新增 Acceptance Criteria")
        for component in item.get("affected_components") or []:
            try:
                _safe_relative_path(Path("."), component)
            except ProjectStateError:
                errors.append(f"{item_id} affected_components 包含非法路径")
    return errors


def create_impact_analysis(
    project_root: str | Path,
    change_request_id: str,
    *,
    item_impacts: list[dict[str, Any]] | None = None,
    risk_level: str | None = None,
    approval_questions: list[str] | None = None,
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "planner",
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    is_v7 = state.get("schema_version") == 7
    if is_v7:
        runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    if state.get("status") != "CHANGE_REQUESTED":
        raise ProjectStateError("只有 CHANGE_REQUESTED 可生成影响分析")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("影响分析必须关联 active_change_request")
    request = load_change_request(root, change_request_id)
    status = current_change_status(root, change_request_id)
    if status not in {"PROPOSED", "ANALYZING"}:
        raise ProjectStateError("只有 PROPOSED/ANALYZING 请求可生成影响分析")
    if status == "PROPOSED" and not is_v7:
        append_event(
            root,
            change_request_id,
            "ANALYZING",
            actor="planner",
            reason="Planner 开始变更影响分析",
        )
    items = item_impacts or [
        _default_item_impact(item) for item in request["requested_changes"]
    ]
    types = {item["type"] for item in request["requested_changes"]}
    inferred_risk = (
        "CRITICAL"
        if "security_change" in types
        else "HIGH"
        if types & {"major_change", "data_migration"}
        else "MEDIUM"
        if types & {"new_feature", "compatibility_change", "scope_change"}
        else "LOW"
    )
    analysis = {
        "schema_version": "1.0",
        "change_request_id": change_request_id,
        "created_at": _now(),
        "risk_level": risk_level or inferred_risk,
        "scope_change": bool(
            types & {"new_feature", "scope_change", "major_change", "behavior_change"}
        ),
        "database_migration_required": any(
            bool(item.get("data_model_impact")) for item in items
        ),
        "new_dependencies_required": any(
            bool(item.get("new_dependency_required")) for item in items
        ),
        "backward_compatibility_risk": (
            "HIGH"
            if "major_change" in types
            else "MEDIUM"
            if types & {"compatibility_change", "data_migration"}
            else "LOW"
            if types & {"new_feature", "behavior_change"}
            else "NONE"
        ),
        "items": items,
        "affected_components": sorted(
            {
                component
                for item in items
                for component in item.get("affected_components") or []
            }
        ),
        "testing_impact": {
            "new_tests_required": bool(
                types
                & {
                    "new_feature",
                    "behavior_change",
                    "bug_fix",
                    "security_change",
                    "data_migration",
                }
            ),
            "regression_required": True,
            "regression_areas": sorted(
                {
                    area
                    for item in items
                    for area in item.get("regression_areas") or []
                }
            ),
        },
        "approval_questions": approval_questions or [],
    }
    errors = _validate_impact_analysis(analysis, request)
    if errors:
        raise ProjectStateError("Impact Analysis 无效：" + "; ".join(errors))
    directory = root / "change_requests" / change_request_id
    analysis_number = len(list(directory.glob("impact-analysis-*.yaml"))) + 1
    yaml_path = directory / f"impact-analysis-{analysis_number:03d}.yaml"
    markdown_path = directory / f"impact-analysis-{analysis_number:03d}.md"
    _write_new_yaml(yaml_path, analysis)
    lines = [
        f"# {change_request_id} 变更影响分析",
        "",
        f"- 风险等级：{analysis['risk_level']}",
        f"- 是否改变范围：{'是' if analysis['scope_change'] else '否'}",
        f"- 是否需要数据迁移：{'是' if analysis['database_migration_required'] else '否'}",
        f"- 是否需要回归：{'是' if analysis['testing_impact']['regression_required'] else '否'}",
        "",
        "## 逐项分析",
        "",
    ]
    for item in items:
        lines.extend(
            [
                f"### {item['change_item_id']}",
                "",
                f"- 分类：{item['classification']}",
                f"- Requirement 动作：{item['requirement_action']}",
                f"- 实施建议：{item['implementation_recommendation']}",
                "",
            ]
        )
    _write_new_text(markdown_path, "\n".join(lines))
    if is_v7 and status == "PROPOSED":
        append_event(
            root,
            change_request_id,
            "ANALYZING",
            actor="planner",
            reason="Planner 开始变更影响分析",
        )
    append_event(
        root,
        change_request_id,
        "WAITING_FOR_APPROVAL",
        actor="planner",
        reason="影响分析已完成，等待明确批准",
        artifact=yaml_path.relative_to(root).as_posix(),
    )
    updated = copy.deepcopy(state)
    updated["status"] = "WAITING_FOR_CHANGE_APPROVAL"
    updated["next_role"] = "planner"
    updated["change_impact_analysis"] = yaml_path.relative_to(root).as_posix()
    if is_v7:
        assert runtime is not None
        _commit_v7_role_step(
            root,
            runtime,
            role="planner",
            source_status="CHANGE_REQUESTED",
            target_status="WAITING_FOR_CHANGE_APPROVAL",
            changed_fields={
                "status": "WAITING_FOR_CHANGE_APPROVAL",
                "next_role": "planner",
                "active_module": None,
                "change_impact_analysis": yaml_path.relative_to(root).as_posix(),
            },
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-impact:{change_request_id}:{yaml_path.stem}",
            worker_id=worker_id,
            run_context=run_context,
        )
        _append_v7_stage_audit(
            root,
            runtime,
            change_request_id=change_request_id,
            stage="IMPACT_ANALYSIS_COMPLETED",
            actor="planner",
            artifact=yaml_path.relative_to(root).as_posix(),
        )
        final_state = load_project_state(root / "project.yaml")
        return {
            "result": "PASS",
            "impact_analysis": str(yaml_path),
            "user_document": str(markdown_path),
            "project_status": final_state["status"],
        }
    errors = validate_project_state(updated, root)
    if errors:
        raise ProjectStateError("影响分析后的项目状态无效：" + "; ".join(errors))
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "impact_analysis": str(yaml_path),
        "user_document": str(markdown_path),
        "project_status": updated["status"],
    }


def _next_plan_version(state: dict[str, Any], plan_directory: Path) -> int:
    current = int(state.get("plan_version") or 0)
    existing = [
        int(match.group(1))
        for path in plan_directory.glob("plan-*.md")
        if (match := re.fullmatch(r"plan-(\d+)", path.stem))
    ]
    return max([current, *existing], default=0) + 1


def _write_change_plan(
    root: Path,
    state: dict[str, Any],
    request: dict[str, Any],
    analysis: dict[str, Any],
    impact_ref: str,
    approval_ref: str,
    approved_ids: set[str],
) -> tuple[Path, int]:
    directory = root / "memory" / "plans"
    version = _next_plan_version(state, directory)
    path = directory / f"plan-{version:03d}.md"
    previous = state.get("approved_plan") or state.get("active_plan")
    lines = [
        f"# 正式变更 Plan v{version:03d}",
        "",
        "## 来源链",
        "",
        f"- Change Request：`change_requests/{request['change_request_id']}.yaml`",
        f"- Impact Analysis：`{impact_ref}`",
        f"- 批准记录：`{approval_ref}`",
        f"- 前一 Plan：`{previous or 'legacy-baseline'}`",
        f"- 基线 Release：`{request['baseline'].get('release_version') or 'legacy-baseline'}`",
        "",
        "## 保持不变的范围",
        "",
        "除下列获批 Change Item 明确修改的 Requirement、Acceptance Criterion、组件与测试外，原正式 Plan 全部保持不变。",
        "",
        "## 获批变更与追踪",
        "",
    ]
    impact_map = {item["change_item_id"]: item for item in analysis["items"]}
    for item in request["requested_changes"]:
        item_id = item["change_item_id"]
        if item_id not in approved_ids:
            continue
        impact = impact_map[item_id]
        new_requirement = impact.get("new_requirement")
        requirement = (
            new_requirement.get("requirement_id")
            if isinstance(new_requirement, dict)
            else ", ".join(impact.get("affected_requirements") or []) or "保持原 Requirement"
        )
        lines.extend(
            [
                f"### {item_id}",
                "",
                f"- 类型：{item['type']}",
                f"- 修改：{item['description']}",
                f"- Requirement：{requirement}",
                f"- 组件：{', '.join(impact.get('affected_components') or []) or '由 Generator 在批准范围内定位'}",
                f"- 回归：{', '.join(impact.get('regression_areas') or []) or '原核心流程与受影响路径'}",
                "",
            ]
        )
    lines.extend(
        [
            "## Generator 边界",
            "",
            "仅实现以上获批 Change Item。不得实现被拒绝项，不得改写旧 Plan、原始反馈、Evaluation Profile 或稳定基线。",
            "",
            "## Evaluator 验收",
            "",
            "逐项验证全部获批 Change Item，并执行原核心功能及受影响区域回归；任一必需项或回归失败均不得 PASS。",
            "",
        ]
    )
    _write_new_text(path, "\n".join(lines))
    return path, version


def decide_change_request(
    project_root: str | Path,
    change_request_id: str,
    *,
    item_decisions: dict[str, str],
    source_text: str,
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "planner",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    is_v7 = state.get("schema_version") == 7
    if is_v7:
        runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    if state.get("status") != "WAITING_FOR_CHANGE_APPROVAL":
        raise ProjectStateError("只有 WAITING_FOR_CHANGE_APPROVAL 可记录决定")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("决定必须关联 active_change_request")
    if not _nonempty(source_text):
        raise ProjectStateError("批准记录必须逐字保存用户决定")
    if current_change_status(root, change_request_id) != "WAITING_FOR_APPROVAL":
        raise ProjectStateError("Change Request 当前不在等待批准状态")
    request = load_change_request(root, change_request_id)
    item_ids = {item["change_item_id"] for item in request["requested_changes"]}
    if set(item_decisions) != item_ids:
        raise ProjectStateError("必须逐项决定所有 Change Item")
    if any(value not in {"APPROVED", "REJECTED"} for value in item_decisions.values()):
        raise ProjectStateError("Item 决定只能是 APPROVED 或 REJECTED")
    approved_ids = {key for key, value in item_decisions.items() if value == "APPROVED"}
    decision = (
        "APPROVED"
        if len(approved_ids) == len(item_ids)
        else "PARTIALLY_APPROVED"
        if approved_ids
        else "REJECTED"
    )
    approvals = root / "change_requests" / change_request_id / "approvals"
    approval_id = _next_identifier(approvals, APPROVAL_ID_RE, "approval-", 3)
    approval = {
        "schema_version": "1.0",
        "approval_id": approval_id,
        "change_request_id": change_request_id,
        "created_at": _now(),
        "decision": decision,
        "item_decisions": [
            {"change_item_id": item_id, "decision": item_decisions[item_id]}
            for item_id in sorted(item_ids)
        ],
        "source_text": source_text,
    }
    approval_path = approvals / f"{approval_id}.yaml"
    _write_new_yaml(approval_path, approval)
    approval_ref = approval_path.relative_to(root).as_posix()
    if not approved_ids:
        append_event(
            root,
            change_request_id,
            "REJECTED",
            actor="user",
            reason="用户拒绝全部修改项",
            artifact=approval_ref,
        )
        previous = state["change_context"]["previous_project_status"]
        updated = copy.deepcopy(state)
        updated["status"] = previous
        updated["next_role"] = None
        updated["active_change_request"] = None
        updated["change_context"] = None
        updated["automatic_retry_allowed"] = False
        write_project_state_atomic(root / "project.yaml", updated)
        return {
            "result": "PASS",
            "decision": "REJECTED",
            "restored_project_status": previous,
            "approval": str(approval_path),
        }
    impact_ref = state.get("change_impact_analysis")
    analysis_path = _safe_relative_path(root, impact_ref, must_exist=True)
    analysis = _load_yaml(analysis_path)
    errors = _validate_impact_analysis(analysis, request)
    if errors:
        raise ProjectStateError("批准前 Impact Analysis 复验失败：" + "; ".join(errors))
    plan_path, plan_version = _write_change_plan(
        root, state, request, analysis, impact_ref, approval_ref, approved_ids
    )
    append_event(
        root,
        change_request_id,
        "APPROVED",
        actor="user",
        reason=f"用户已批准 {len(approved_ids)}/{len(item_ids)} 个修改项",
        artifact=approval_ref,
    )
    updated = copy.deepcopy(state)
    plan_ref = plan_path.relative_to(root).as_posix()
    updated["plan_version"] = plan_version
    updated["active_plan"] = plan_ref
    updated["approved_plan"] = plan_ref
    updated["plan_status"] = "approved"
    updated["plan_approval_status"] = "approved"
    updated["plan_approval_record"] = approval_ref
    updated["change_approval_record"] = approval_ref
    updated["approved_change_items"] = sorted(approved_ids)
    updated["next_role"] = "planner"
    if is_v7:
        assert runtime is not None
        _commit_v7_role_state(
            root,
            state,
            runtime,
            role="planner",
            next_state=updated,
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-scope-approved:{change_request_id}:{approval_id}",
            worker_id=worker_id,
        )
        _append_v7_stage_audit(
            root,
            runtime,
            change_request_id=change_request_id,
            stage="CHANGE_SCOPE_APPROVED",
            actor="user",
            artifact=approval_ref,
        )
        final_state = load_project_state(root / "project.yaml")
        return {
            "result": "PASS",
            "decision": decision,
            "approved_change_items": sorted(approved_ids),
            "approval": str(approval_path),
            "plan": str(plan_path),
            "plan_version": plan_version,
            "project_status": final_state["status"],
        }
    errors = validate_project_state(updated, root)
    if errors:
        raise ProjectStateError("批准后的项目状态无效：" + "; ".join(errors))
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "decision": decision,
        "approved_change_items": sorted(approved_ids),
        "approval": str(approval_path),
        "plan": str(plan_path),
        "plan_version": plan_version,
    }


def request_impact_analysis_revision(
    project_root: str | Path,
    change_request_id: str,
    *,
    source_text: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    if state.get("status") != "WAITING_FOR_CHANGE_APPROVAL":
        raise ProjectStateError("只有等待批准状态可要求修改影响分析")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("修订必须关联 active_change_request")
    if current_change_status(root, change_request_id) != "WAITING_FOR_APPROVAL":
        raise ProjectStateError("Change Request 当前不在等待批准状态")
    if not _nonempty(source_text):
        raise ProjectStateError("必须逐字保存修订意见")
    request = load_change_request(root, change_request_id)
    approvals = root / "change_requests" / change_request_id / "approvals"
    approval_id = _next_identifier(approvals, APPROVAL_ID_RE, "approval-", 3)
    record = {
        "schema_version": "1.0",
        "approval_id": approval_id,
        "change_request_id": change_request_id,
        "created_at": _now(),
        "decision": "REVISION_REQUESTED",
        "item_decisions": [
            {
                "change_item_id": item["change_item_id"],
                "decision": "PENDING",
            }
            for item in request["requested_changes"]
        ],
        "source_text": source_text,
    }
    path = approvals / f"{approval_id}.yaml"
    _write_new_yaml(path, record)
    append_event(
        root,
        change_request_id,
        "ANALYZING",
        actor="user",
        reason="用户要求修改变更影响分析",
        artifact=path.relative_to(root).as_posix(),
    )
    updated = copy.deepcopy(state)
    updated["status"] = "CHANGE_REQUESTED"
    updated["next_role"] = "planner"
    updated["automatic_retry_allowed"] = False
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "revision_record": str(path),
        "project_status": "CHANGE_REQUESTED",
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _baseline_files(root: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for directory_name in ("code", "tests"):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
                continue
            files.append({"path": relative, "sha256": _sha256(path)})
    return files


def create_change_baseline(
    project_root: str | Path, change_request_id: str
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    request = load_change_request(root, change_request_id)
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("基线必须关联 active_change_request")
    if current_change_status(root, change_request_id) != "APPROVED":
        raise ProjectStateError("只有已批准 Change Request 可以建立实施基线")
    manifest = {
        "schema_version": "1.0",
        "change_request_id": change_request_id,
        "created_at": _now(),
        "release_version": request["baseline"].get("release_version"),
        "plan_version": request["baseline"].get("plan_version"),
        "last_evaluation": request["baseline"].get("last_evaluation"),
        "commit_sha": request["baseline"].get("commit_sha"),
        "hash_algorithm": "sha256",
        "files": _baseline_files(root),
    }
    path = (
        root
        / "change_requests"
        / change_request_id
        / "baseline"
        / "manifest.yaml"
    )
    _write_new_yaml(path, manifest)
    return {
        "result": "PASS",
        "baseline": str(path),
        "file_count": len(manifest["files"]),
        "rollback": (
            f"恢复 Git commit {manifest['commit_sha']}"
            if manifest["commit_sha"]
            else "依据 SHA-256 Manifest 和原 Release 工件执行人工恢复"
        ),
    }


def begin_change_implementation(
    project_root: str | Path,
    change_request_id: str,
    *,
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "generator",
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    is_v7 = state.get("schema_version") == 7
    if is_v7:
        runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    if state.get("status") != "WAITING_FOR_CHANGE_APPROVAL":
        raise ProjectStateError("项目尚未处于批准后的变更等待状态")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("实施必须关联 active_change_request")
    approved = state.get("approved_change_items")
    if not isinstance(approved, list) or not approved:
        raise ProjectStateError("未找到已批准 Change Item")
    if not state.get("change_approval_record") or not state.get("approved_plan"):
        raise ProjectStateError("批准来源链或新正式 Plan 不完整")
    baseline_path = (
        root
        / "change_requests"
        / change_request_id
        / "baseline"
        / "manifest.yaml"
    )
    if baseline_path.exists():
        baseline = _load_yaml(baseline_path)
        if baseline.get("change_request_id") != change_request_id:
            raise ProjectStateError("现有基线关联错误")
    else:
        create_change_baseline(root, change_request_id)
    append_event(
        root,
        change_request_id,
        "IMPLEMENTING",
        actor="generator",
        reason="批准来源链与稳定基线已验证，Generator 可以开始实施",
        artifact=baseline_path.relative_to(root).as_posix(),
    )
    updated = copy.deepcopy(state)
    updated["status"] = "IMPLEMENTING"
    updated["next_role"] = "generator"
    updated["change_baseline"] = baseline_path.relative_to(root).as_posix()
    updated["automatic_retry_allowed"] = True
    if is_v7:
        assert runtime is not None
        _commit_v7_role_transition(
            root,
            state,
            runtime,
            role="generator",
            source_status="WAITING_FOR_CHANGE_APPROVAL",
            target_status="IMPLEMENTING",
            changed_fields={
                "status": "IMPLEMENTING",
                "next_role": "generator",
                "active_module": None,
                "change_baseline": baseline_path.relative_to(root).as_posix(),
                "automatic_retry_allowed": True,
            },
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-implementation:{change_request_id}",
            worker_id=worker_id,
        )
        _append_v7_stage_audit(
            root,
            runtime,
            change_request_id=change_request_id,
            stage="CHANGE_IMPLEMENTATION_STARTED",
            actor="generator",
            artifact=baseline_path.relative_to(root).as_posix(),
        )
        final_state = load_project_state(root / "project.yaml")
        return {
            "result": "PASS",
            "project_status": final_state["status"],
            "baseline": str(baseline_path),
            "approved_change_items": approved,
        }
    errors = validate_project_state(updated, root)
    if errors:
        raise ProjectStateError("实施状态无效：" + "; ".join(errors))
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "project_status": "IMPLEMENTING",
        "baseline": str(baseline_path),
        "approved_change_items": approved,
    }


def validate_generator_change_scope(
    project_root: str | Path,
    change_request_id: str,
    *,
    implemented_items: Iterable[str],
    changed_files: Iterable[str],
) -> list[str]:
    root = Path(project_root).resolve()
    errors: list[str] = []
    state = load_project_state(root / "project.yaml")
    if state.get("status") != "IMPLEMENTING" or state.get("next_role") != "generator":
        errors.append("Generator 只能在 IMPLEMENTING 状态工作")
    if state.get("active_change_request") != change_request_id:
        errors.append("Generator 关联的 Change Request 不是当前活动请求")
    approved = set(state.get("approved_change_items") or [])
    implemented = set(implemented_items)
    if not implemented:
        errors.append("Generator Handoff 至少需要一个 implemented_item")
    if implemented - approved:
        errors.append("Generator 试图实现未批准 Change Item")
    if approved - implemented:
        errors.append("Generator Handoff 未逐项回应全部批准 Change Item")
    protected_prefixes = (
        "memory/plans/",
        "memory/proposals/",
        "memory/requirements/",
        "evaluation/",
        "releases/",
        "change_requests/",
        "config/evaluation_rules/",
    )
    normalized_files: list[str] = []
    for reference in changed_files:
        try:
            path = _safe_relative_path(root, reference, must_exist=True)
        except ProjectStateError as exc:
            errors.append(str(exc))
            continue
        relative = path.relative_to(root).as_posix()
        normalized_files.append(relative)
        if relative.startswith(protected_prefixes):
            errors.append(f"Generator 修改了受保护文件：{relative}")
        if not relative.startswith(("code/", "tests/", "artifacts/")):
            errors.append(f"Generator 修改路径超出允许范围：{relative}")
    if not normalized_files:
        errors.append("Generator Handoff 必须记录真实修改文件")
    baseline_path = (
        root
        / "change_requests"
        / change_request_id
        / "baseline"
        / "manifest.yaml"
    )
    if not baseline_path.is_file():
        errors.append("缺少稳定基线 Manifest")
    return errors


def record_generator_change_handoff(
    project_root: str | Path,
    change_request_id: str,
    *,
    implemented_items: list[dict[str, Any]],
    changed_files: list[str],
    verification_results: list[dict[str, Any]],
    known_limitations: list[str] | None = None,
    rollback: str,
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "generator",
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    is_v7 = state.get("schema_version") == 7
    if is_v7:
        runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    item_ids = [item.get("change_item_id") for item in implemented_items]
    errors = validate_generator_change_scope(
        root,
        change_request_id,
        implemented_items=[str(item_id) for item_id in item_ids],
        changed_files=changed_files,
    )
    if any(
        not isinstance(item, dict)
        or not _nonempty(item.get("change_item_id"))
        or not _nonempty(item.get("implementation"))
        for item in implemented_items
    ):
        errors.append("implemented_items 必须逐项包含 ID 和 implementation")
    if not verification_results or any(
        not isinstance(result, dict)
        or not _nonempty(result.get("command"))
        or result.get("status") not in {"PASS", "FAIL", "BLOCKED"}
        for result in verification_results
    ):
        errors.append("verification_results 必须包含命令和确定性状态")
    if any(result.get("status") != "PASS" for result in verification_results):
        errors.append("Generator 自测未全部 PASS")
    if not _nonempty(rollback):
        errors.append("Handoff 必须说明回滚方式")
    if errors:
        raise ProjectStateError("Generator Handoff 无效：" + "; ".join(errors))
    normalized_files = [
        _safe_relative_path(root, reference, must_exist=True)
        .relative_to(root)
        .as_posix()
        for reference in changed_files
    ]
    baseline_path = (
        root
        / "change_requests"
        / change_request_id
        / "baseline"
        / "manifest.yaml"
    )
    baseline = _load_yaml(baseline_path)
    changed_hashes = [
        {"path": reference, "sha256": _sha256(root / reference)}
        for reference in normalized_files
    ]
    baseline_hashes = {
        item["path"]: item["sha256"] for item in baseline.get("files", [])
    }
    if all(
        baseline_hashes.get(item["path"]) == item["sha256"]
        for item in changed_hashes
    ):
        raise ProjectStateError("Handoff 声明的文件与稳定基线相比没有变化")
    handoff_directory = root / "change_requests" / change_request_id / "handoffs"
    handoff_id = _next_identifier(
        handoff_directory, HANDOFF_ID_RE, "handoff-", 3
    )
    handoff = {
        "schema_version": "1.0",
        "handoff_id": handoff_id,
        "change_request_id": change_request_id,
        "created_at": _now(),
        "implemented_items": implemented_items,
        "changed_files": normalized_files,
        "changed_file_hashes": changed_hashes,
        "verification_results": verification_results,
        "known_limitations": known_limitations or [],
        "rollback": rollback,
    }
    path = (
        root
        / "change_requests"
        / change_request_id
        / "handoffs"
        / f"{handoff_id}.yaml"
    )
    _write_new_yaml(path, handoff)
    append_event(
        root,
        change_request_id,
        "EVALUATING",
        actor="generator",
        reason="Generator 已逐项交接批准范围及自测证据",
        artifact=path.relative_to(root).as_posix(),
    )
    updated = copy.deepcopy(state)
    updated["status"] = "EVALUATING"
    updated["next_role"] = "evaluator"
    updated["last_generator_response"] = path.relative_to(root).as_posix()
    if is_v7:
        assert runtime is not None
        _commit_v7_role_step(
            root,
            runtime,
            role="generator",
            source_status="IMPLEMENTING",
            target_status="EVALUATING",
            changed_fields={
                "status": "EVALUATING",
                "next_role": "evaluator",
                "active_module": None,
                "last_generator_response": path.relative_to(root).as_posix(),
            },
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-handoff:{change_request_id}:{handoff_id}",
            worker_id=worker_id,
            run_context=run_context,
        )
        _append_v7_stage_audit(
            root,
            runtime,
            change_request_id=change_request_id,
            stage="GENERATOR_HANDOFF",
            actor="generator",
            artifact=path.relative_to(root).as_posix(),
        )
        final_state = load_project_state(root / "project.yaml")
        return {
            "result": "PASS",
            "handoff": str(path),
            "project_status": final_state["status"],
        }
    errors = validate_project_state(updated, root)
    if errors:
        raise ProjectStateError("Generator 交接后的项目状态无效：" + "; ".join(errors))
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "handoff": str(path),
        "project_status": "EVALUATING",
    }


def _validate_current_handoff(root: Path, state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    reference = state.get("last_generator_response")
    if not reference:
        return ["缺少当前 Generator Handoff"]
    try:
        path = _safe_relative_path(root, reference, must_exist=True)
    except ProjectStateError as exc:
        return [str(exc)]
    handoff = _load_yaml(path)
    for item in handoff.get("changed_file_hashes") or []:
        if not isinstance(item, dict) or not _nonempty(item.get("path")):
            errors.append("Generator Handoff 文件哈希结构无效")
            continue
        try:
            changed = _safe_relative_path(root, item["path"], must_exist=True)
        except ProjectStateError as exc:
            errors.append(str(exc))
            continue
        if _sha256(changed) != item.get("sha256"):
            errors.append(f"Generator Handoff 后文件哈希发生变化：{item['path']}")
    return errors


def record_change_evaluation(
    project_root: str | Path,
    change_request_id: str,
    *,
    change_item_results: list[dict[str, Any]],
    regression_results: list[dict[str, Any]],
    evidence: list[str],
    environment_blocked: bool = False,
    ambiguity_reason: str | None = None,
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "evaluator",
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    is_v7 = state.get("schema_version") == 7
    if is_v7:
        runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    if state.get("status") != "EVALUATING" or state.get("next_role") != "evaluator":
        raise ProjectStateError("Evaluator 只能在 EVALUATING 状态验收变更")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("Evaluation 必须关联 active_change_request")
    if current_change_status(root, change_request_id) != "EVALUATING":
        raise ProjectStateError("Change Request 当前不在 EVALUATING")
    approved = set(state.get("approved_change_items") or [])
    result_ids = {
        item.get("change_item_id")
        for item in change_item_results
        if isinstance(item, dict)
    }
    errors: list[str] = []
    if result_ids != approved:
        errors.append("Evaluation 必须逐项覆盖所有且仅覆盖批准 Change Item")
    for item in change_item_results:
        if not isinstance(item, dict):
            errors.append("change_item_results 每项必须是对象")
            continue
        if item.get("status") not in {"PASS", "FAIL", "BLOCKED"}:
            errors.append("Change Item 验收状态无效")
        if not _nonempty(item.get("requirement_id")):
            errors.append("Change Item 验收必须关联 Requirement ID")
        if not item.get("acceptance_criterion_ids"):
            errors.append("Change Item 验收必须关联 Acceptance Criterion ID")
        if not item.get("evidence_ids"):
            errors.append("Change Item 验收必须关联证据")
    if not regression_results:
        errors.append("必须执行原功能回归验收")
    for regression in regression_results:
        if not isinstance(regression, dict) or regression.get("status") not in {
            "PASS",
            "FAIL",
            "BLOCKED",
        }:
            errors.append("Regression 结果结构或状态无效")
        elif not _nonempty(regression.get("regression_id")) or not regression.get(
            "evidence_ids"
        ):
            errors.append("Regression 必须包含 ID 和证据")
    if not evidence or any(not _nonempty(item) for item in evidence):
        errors.append("Evaluation 必须提供非空证据清单")
    errors.extend(_validate_current_handoff(root, state))
    baseline_ref = state.get("change_baseline")
    try:
        baseline_path = _safe_relative_path(root, baseline_ref, must_exist=True)
        baseline = _load_yaml(baseline_path)
        if baseline.get("change_request_id") != change_request_id:
            errors.append("基线与 Change Request 关联不一致")
    except ProjectStateError as exc:
        errors.append(str(exc))
        baseline_path = root
    if errors:
        raise ProjectStateError("Change Evaluation 无效：" + "; ".join(errors))
    combined = [*change_item_results, *regression_results]
    any_blocked = environment_blocked or any(
        item.get("status") == "BLOCKED" for item in combined
    )
    any_failed = any(item.get("status") == "FAIL" for item in combined)
    if any_blocked:
        result = "BLOCKED"
    elif ambiguity_reason:
        result = "PLANNER_ROUTE"
    elif any_failed:
        result = "FAIL"
    else:
        result = "PASS"
    directory = root / "change_requests" / change_request_id / "evaluations"
    evaluation_id = _next_identifier(
        directory, EVALUATION_ID_RE, "evaluation-", 3
    )
    evaluation = {
        "schema_version": "1.0",
        "evaluation_id": evaluation_id,
        "change_request_id": change_request_id,
        "created_at": _now(),
        "change_item_results": change_item_results,
        "regression_results": regression_results,
        "evidence": evidence,
        "baseline_comparison": {
            "baseline_manifest": baseline_path.relative_to(root).as_posix(),
            "generator_handoff_verified": True,
        },
        "result": result,
    }
    path = directory / f"{evaluation_id}.yaml"
    _write_new_yaml(path, evaluation)
    reference = path.relative_to(root).as_posix()
    updated = copy.deepcopy(state)
    updated["last_evaluation"] = reference
    iteration = int((state.get("change_context") or {}).get("evaluation_iteration", 0))
    if result == "PASS":
        append_event(
            root,
            change_request_id,
            "RELEASE_READY",
            actor="evaluator",
            reason="全部批准变更与必需回归已通过",
            artifact=reference,
        )
        updated["status"] = "RELEASE_READY"
        updated["next_role"] = "evaluator"
        updated["automatic_retry_allowed"] = False
    elif result == "FAIL":
        iteration += 1
        updated["current_iteration"] = iteration
        updated["change_context"]["evaluation_iteration"] = iteration
        if iteration >= 5:
            append_event(
                root,
                change_request_id,
                "WAITING_FOR_USER",
                actor="evaluator",
                reason="当前 Change Request 已达到五次验收返工上限",
                artifact=reference,
            )
            updated["status"] = "WAITING_FOR_USER"
            updated["next_role"] = None
            updated["automatic_retry_allowed"] = False
        else:
            append_event(
                root,
                change_request_id,
                "IMPLEMENTING",
                actor="evaluator",
                reason="变更或原功能回归失败，返回 Generator 定向修复",
                artifact=reference,
            )
            updated["status"] = "IMPLEMENTING"
            updated["next_role"] = "generator"
            updated["automatic_retry_allowed"] = True
    elif result == "BLOCKED":
        append_event(
            root,
            change_request_id,
            "BLOCKED",
            actor="evaluator",
            reason="验收环境或必需证据阻塞",
            artifact=reference,
        )
        updated["status"] = "BLOCKED"
        updated["next_role"] = None
        updated["automatic_retry_allowed"] = False
        updated["blocked_reason"] = "change_evaluation_blocked"
    else:
        append_event(
            root,
            change_request_id,
            "ANALYZING",
            actor="evaluator",
            reason=ambiguity_reason or "变更范围需要 Planner 重新澄清",
            artifact=reference,
        )
        updated["status"] = "CHANGE_REQUESTED"
        updated["next_role"] = "planner"
        updated["automatic_retry_allowed"] = False
    if is_v7:
        assert runtime is not None
        changed_fields = {
            field: copy.deepcopy(updated.get(field))
            for field in ("status", "next_role", "active_module")
        }
        changed_fields.update(
            {
                field: copy.deepcopy(updated.get(field))
                for field in (
                    "last_evaluation",
                    "current_iteration",
                    "change_context",
                    "automatic_retry_allowed",
                    "blocked_reason",
                )
                if updated.get(field) != state.get(field)
            }
        )
        _commit_v7_role_step(
            root,
            runtime,
            role="evaluator",
            source_status="EVALUATING",
            target_status=str(updated["status"]),
            changed_fields=changed_fields,
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-evaluation:{change_request_id}:{evaluation_id}",
            worker_id=worker_id,
            run_context=run_context,
        )
        _append_v7_stage_audit(
            root,
            runtime,
            change_request_id=change_request_id,
            stage=f"EVALUATION_{result}",
            actor="evaluator",
            artifact=reference,
        )
        final_state = load_project_state(root / "project.yaml")
        return {
            "result": result,
            "evaluation": str(path),
            "project_status": final_state["status"],
            "evaluation_iteration": iteration,
        }
    state_errors = validate_project_state(updated, root)
    if state_errors:
        raise ProjectStateError(
            "Evaluation 路由后的项目状态无效：" + "; ".join(state_errors)
        )
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": result,
        "evaluation": str(path),
        "project_status": updated["status"],
        "evaluation_iteration": iteration,
    }


def _next_release_version(current: str | None, change_types: set[str]) -> tuple[str, str]:
    if not current or current == "legacy-baseline":
        return "1.0.0", "legacy_baseline"
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current)
    if not match:
        raise ProjectStateError(
            "当前项目已有非 SemVer 版本，但未声明可执行的 release_policy"
        )
    major, minor, patch = map(int, match.groups())
    if "major_change" in change_types:
        return f"{major + 1}.0.0", "minimal_semver"
    if "new_feature" in change_types:
        return f"{major}.{minor + 1}.0", "minimal_semver"
    return f"{major}.{minor}.{patch + 1}", "minimal_semver"


def _ensure_baseline_release(
    root: Path, state: dict[str, Any], request: dict[str, Any]
) -> Path | None:
    previous = state.get("release_version") or request["baseline"].get("release_version")
    if not previous:
        return None
    path = root / "releases" / f"release-{previous}.yaml"
    if path.exists():
        return path
    baseline = {
        "schema_version": "1.0",
        "release_version": previous,
        "previous_release": None,
        "change_request_id": None,
        "plan_version": request["baseline"].get("plan_version"),
        "evaluation_id": None,
        "created_at": _now(),
        "status": "BASELINE",
        "version_source": "legacy_baseline",
        "baseline_manifest": None,
        "commit_sha": request["baseline"].get("commit_sha"),
        "note": "首次 Change Request 时补建的迁移基线，不表示该 Release 记录过去真实存在",
    }
    _write_new_yaml(path, baseline)
    return path


def create_change_release(
    project_root: str | Path,
    change_request_id: str,
    *,
    runtime: Any | None = None,
    control_plane_home: str | Path | None = None,
    worker_id: str = "evaluator",
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    is_v7 = state.get("schema_version") == 7
    if is_v7:
        runtime = _runtime_orchestrator(root, state, runtime, control_plane_home)
    if state.get("status") != "RELEASE_READY" or state.get("next_role") != "evaluator":
        raise ProjectStateError("只有 RELEASE_READY 可以创建新 Release")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("Release 必须关联 active_change_request")
    if current_change_status(root, change_request_id) != "RELEASE_READY":
        raise ProjectStateError("Change Request 尚未通过 Release Gate")
    evaluation_ref = state.get("last_evaluation")
    evaluation_path = _safe_relative_path(root, evaluation_ref, must_exist=True)
    evaluation = _load_yaml(evaluation_path)
    if evaluation.get("change_request_id") != change_request_id:
        raise ProjectStateError("Release 引用的 Evaluation 关联错误")
    if evaluation.get("result") != "PASS":
        raise ProjectStateError("只有 PASS Evaluation 可以创建 Release")
    request = load_change_request(root, change_request_id)
    _ensure_baseline_release(root, state, request)
    approved = set(state.get("approved_change_items") or [])
    change_types = {
        item["type"]
        for item in request["requested_changes"]
        if item["change_item_id"] in approved
    }
    previous = state.get("release_version") or request["baseline"].get("release_version")
    version, source = _next_release_version(previous, change_types)
    path = root / "releases" / f"release-{version}.yaml"
    record = {
        "schema_version": "1.0",
        "release_version": version,
        "previous_release": previous or "legacy-baseline",
        "change_request_id": change_request_id,
        "plan_version": state.get("plan_version"),
        "evaluation_id": evaluation["evaluation_id"],
        "created_at": _now(),
        "status": "ACCEPTED",
        "version_source": source,
        "baseline_manifest": state.get("change_baseline"),
        "commit_sha": _git_commit(root / "code")
        if (root / "code").is_dir()
        else _git_commit(root),
        "note": "Change Request 与原功能回归均通过后创建",
    }
    _write_new_yaml(path, record)
    append_event(
        root,
        change_request_id,
        "ACCEPTED",
        actor="evaluator",
        reason=f"Release {version} 已原子创建并验证",
        artifact=path.relative_to(root).as_posix(),
    )
    updated = copy.deepcopy(state)
    updated["status"] = "ACCEPTED"
    updated["next_role"] = None
    updated["active_change_request"] = None
    updated["change_context"] = None
    updated["release_version"] = version
    updated["current_release"] = path.relative_to(root).as_posix()
    updated["current_iteration"] = 0
    updated["automatic_retry_allowed"] = False
    updated["blocked_reason"] = None
    if is_v7:
        assert runtime is not None
        _commit_v7_role_transition(
            root,
            state,
            runtime,
            role="evaluator",
            source_status="RELEASE_READY",
            target_status="ACCEPTED",
            changed_fields={
                "status": "ACCEPTED",
                "next_role": None,
                "active_module": None,
                "active_change_request": None,
                "change_context": None,
                "release_version": version,
                "current_release": path.relative_to(root).as_posix(),
                "current_iteration": 0,
                "automatic_retry_allowed": False,
                "blocked_reason": None,
            },
            expected_revision=int(state["runtime"]["revision"]),
            idempotency_key=f"change-request-release:{change_request_id}:{version}",
            worker_id=worker_id,
        )
        _append_v7_stage_audit(
            root,
            runtime,
            change_request_id=change_request_id,
            stage="RELEASE_ACCEPTED",
            actor="evaluator",
            artifact=path.relative_to(root).as_posix(),
        )
        final_state = load_project_state(root / "project.yaml")
        return {
            "result": "PASS",
            "release_version": version,
            "release": str(path),
            "previous_release": previous or "legacy-baseline",
            "project_status": final_state["status"],
        }
    errors = validate_project_state(updated, root)
    if errors:
        raise ProjectStateError("Release 后项目状态无效：" + "; ".join(errors))
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "release_version": version,
        "release": str(path),
        "previous_release": previous or "legacy-baseline",
        "project_status": "ACCEPTED",
    }


def complete_release_rollback(
    project_root: str | Path,
    target_release: str,
    *,
    reason: str,
    verification_evidence: list[str],
    restoration_verified: bool,
) -> dict[str, Any]:
    """记录已完成且已验证的 Release 回滚，不自行假装恢复代码。"""

    root = Path(project_root).resolve()
    state = load_project_state(root / "project.yaml")
    if state.get("status") not in REOPENABLE_STATUSES:
        raise ProjectStateError("只能从稳定完成状态执行 Release 回滚")
    if not restoration_verified or not verification_evidence:
        raise ProjectStateError("更新当前 Release 前必须提供真实恢复与回归证据")
    target_path = root / "releases" / f"release-{target_release}.yaml"
    target = _load_yaml(target_path)
    if target.get("release_version") != target_release:
        raise ProjectStateError("目标 Release 记录与版本不一致")
    directory = root / "releases" / "rollbacks"
    rollback_id = _next_identifier(directory, ROLLBACK_ID_RE, "rollback-", 3)
    record = {
        "schema_version": "1.0",
        "rollback_id": rollback_id,
        "created_at": _now(),
        "from_release": state.get("release_version"),
        "target_release": target_release,
        "reason": reason,
        "verification_evidence": verification_evidence,
        "restoration_verified": True,
        "status": "COMPLETED",
    }
    path = directory / f"{rollback_id}.yaml"
    _write_new_yaml(path, record)
    updated = copy.deepcopy(state)
    updated["release_version"] = target_release
    updated["current_release"] = target_path.relative_to(root).as_posix()
    updated["last_release_rollback"] = path.relative_to(root).as_posix()
    write_project_state_atomic(root / "project.yaml", updated)
    return {
        "result": "PASS",
        "target_release": target_release,
        "rollback_record": str(path),
    }


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="已完成项目 Change Request 工作流")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("project_root", type=Path)
    create.add_argument("--feedback", required=True)
    create.add_argument("--change", action="append", required=True)
    cancel = sub.add_parser("cancel")
    cancel.add_argument("project_root", type=Path)
    cancel.add_argument("change_request_id")
    cancel.add_argument("--reason", required=True)
    recover = sub.add_parser("recover")
    recover.add_argument("project_root", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = create_change_request(
                args.project_root,
                raw_feedback=args.feedback,
                requested_changes=args.change,
            )
        elif args.action == "cancel":
            result = cancel_change_request(
                args.project_root, args.change_request_id, reason=args.reason
            )
        else:
            result = recover_change_request_state(args.project_root)
    except (OSError, ProjectStateError) as exc:
        print(json.dumps({"result": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("result") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(_run_cli())
