"""RA8 Change Request Reference 绑定协议。

Reference 绑定只追加 Change Request 专属记录，不会重写原项目 Reference、
不重新初始化 First-Ask，也不代替用户批准变更范围。
"""

from __future__ import annotations

import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

try:
    from scripts.change_request import current_change_status, load_change_request
except ModuleNotFoundError:
    # 兼容 change_request.py 仍使用裸 project_state 导入的脚本执行方式。
    scripts_root = str(Path(__file__).resolve().parent)
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    from change_request import current_change_status, load_change_request
from scripts.project_state import ProjectStateError, load_project_state


_CR_ID = re.compile(r"^CR-[0-9]{4}$")
_REF_ID = re.compile(r"^REF-[0-9]{3}$")
_BINDING_ID = re.compile(r"^CRREF-[0-9]{4}$")
_APPROVAL_ID = re.compile(r"^approval-[0-9]{3}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _write_once(path: Path, record: Mapping[str, Any]) -> None:
    if path.exists():
        raise ProjectStateError("REFERENCE_CR_ARTIFACT_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(record), allow_unicode=True, sort_keys=False), encoding="utf-8")


def _reference_root(root: Path, change_request_id: str) -> Path:
    if not _CR_ID.fullmatch(change_request_id):
        raise ProjectStateError("REFERENCE_CR_ID_INVALID")
    return root / "change_requests" / change_request_id / "references"


def _assert_active_change_request(root: Path, change_request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    state = load_project_state(root / "project.yaml")
    if state.get("status") == "ACCEPTED" and state.get("active_change_request") is None:
        raise ProjectStateError("REFERENCE_CR_REQUIRES_ACTIVE_CHANGE_REQUEST")
    if state.get("active_change_request") != change_request_id:
        raise ProjectStateError("REFERENCE_CR_ACTIVE_REQUEST_MISMATCH")
    request = load_change_request(root, change_request_id)
    if request.get("project_id") != state.get("project_id"):
        raise ProjectStateError("REFERENCE_CR_PROJECT_MISMATCH")
    if current_change_status(root, change_request_id) in {"CANCELLED", "REJECTED", "ACCEPTED"}:
        raise ProjectStateError("REFERENCE_CR_TERMINAL_REQUEST")
    return state, request


def _next_binding_id(directory: Path) -> str:
    highest = 0
    if directory.exists():
        for path in directory.glob("reference-binding-*.yaml"):
            match = re.fullmatch(r"reference-binding-([0-9]{4})\.yaml", path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"CRREF-{highest + 1:04d}"


def validate_reference_change_binding(record: Mapping[str, Any], *, project_id: str | None = None) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "binding_id", "change_request_id", "project_id", "reference_ids",
        "synthesis_ref", "synthesis_version", "approved_change_scope", "change_scope_approval_ref",
        "baseline_manifest", "status", "supersedes", "created_at", "trust_level",
    }
    if set(record) != required:
        errors.append("REFERENCE_CR_BINDING_FIELDS_INVALID")
    if record.get("schema_version") != 1:
        errors.append("REFERENCE_CR_BINDING_SCHEMA_INVALID")
    if not _BINDING_ID.fullmatch(str(record.get("binding_id"))):
        errors.append("REFERENCE_CR_BINDING_ID_INVALID")
    if not _CR_ID.fullmatch(str(record.get("change_request_id"))):
        errors.append("REFERENCE_CR_ID_INVALID")
    if project_id is not None and record.get("project_id") != project_id:
        errors.append("REFERENCE_CR_BINDING_PROJECT_MISMATCH")
    refs = record.get("reference_ids")
    if not isinstance(refs, list) or not refs or not all(_REF_ID.fullmatch(str(item)) for item in refs):
        errors.append("REFERENCE_CR_BINDING_REFERENCES_INVALID")
    if not isinstance(record.get("synthesis_ref"), str) or not record["synthesis_ref"].startswith("change_requests/"):
        errors.append("REFERENCE_CR_BINDING_SYNTHESIS_INVALID")
    if not isinstance(record.get("synthesis_version"), int) or record["synthesis_version"] < 1:
        errors.append("REFERENCE_CR_BINDING_SYNTHESIS_VERSION_INVALID")
    scope = record.get("approved_change_scope")
    if not isinstance(scope, list) or not scope or not all(isinstance(item, str) and item for item in scope):
        errors.append("REFERENCE_CR_BINDING_SCOPE_INVALID")
    if not _APPROVAL_ID.fullmatch(str(record.get("change_scope_approval_ref"))):
        errors.append("REFERENCE_CR_BINDING_APPROVAL_INVALID")
    if not isinstance(record.get("baseline_manifest"), str) or not record["baseline_manifest"].startswith("change_requests/"):
        errors.append("REFERENCE_CR_BINDING_BASELINE_INVALID")
    if record.get("status") not in {"ACTIVE", "REVOKED"}:
        errors.append("REFERENCE_CR_BINDING_STATUS_INVALID")
    if record.get("supersedes") is not None and not _BINDING_ID.fullmatch(str(record["supersedes"])):
        errors.append("REFERENCE_CR_BINDING_SUPERSEDES_INVALID")
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        errors.append("REFERENCE_CR_BINDING_TIMESTAMP_INVALID")
    if record.get("trust_level") != "untrusted":
        errors.append("REFERENCE_CR_BINDING_TRUST_INVALID")
    return errors


def _load_history(directory: Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    if not directory.exists():
        return values
    for path in sorted(directory.glob("reference-binding-*.yaml")):
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ProjectStateError("REFERENCE_CR_BINDING_INVALID")
        errors = validate_reference_change_binding(value)
        if errors:
            raise ProjectStateError("REFERENCE_CR_BINDING_INVALID:" + ",".join(errors))
        values.append(value)
    return values


def register_reference_change_request(
    project_root: str | Path,
    change_request_id: str,
    *,
    reference_ids: Iterable[str],
    synthesis_ref: str,
    synthesis_version: int,
    approved_change_scope: Iterable[str],
    change_scope_approval_ref: str,
    baseline_manifest: str,
) -> dict[str, Any]:
    """给活动 CR 追加一个 Reference binding；这个动作不等于批准变更。"""

    root = Path(project_root).resolve()
    state, _ = _assert_active_change_request(root, change_request_id)
    refs = list(dict.fromkeys(str(item) for item in reference_ids))
    scope = list(dict.fromkeys(str(item) for item in approved_change_scope))
    directory = _reference_root(root, change_request_id)
    binding_id = _next_binding_id(directory)
    history = _load_history(directory)
    previous = next((item for item in reversed(history) if item.get("status") == "ACTIVE"), None)
    record = {
        "schema_version": 1,
        "binding_id": binding_id,
        "change_request_id": change_request_id,
        "project_id": state["project_id"],
        "reference_ids": refs,
        "synthesis_ref": str(synthesis_ref).replace("\\", "/"),
        "synthesis_version": synthesis_version,
        "approved_change_scope": scope,
        "change_scope_approval_ref": change_scope_approval_ref,
        "baseline_manifest": str(baseline_manifest).replace("\\", "/"),
        "status": "ACTIVE",
        "supersedes": previous.get("binding_id") if previous else None,
        "created_at": _now(),
        "trust_level": "untrusted",
    }
    errors = validate_reference_change_binding(record, project_id=str(state["project_id"]))
    if errors:
        raise ProjectStateError("REFERENCE_CR_BINDING_INVALID:" + ",".join(errors))
    path = directory / f"reference-binding-{int(binding_id.split('-')[1]):04d}.yaml"
    _write_once(path, record)
    record["artifact_ref"] = path.relative_to(root).as_posix()
    return record


def revoke_reference_change_binding(
    project_root: str | Path,
    change_request_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """追加撤销快照，保留原始 binding，不删除历史。"""

    if not isinstance(reason, str) or not reason.strip():
        raise ProjectStateError("REFERENCE_CR_REVOKE_REASON_REQUIRED")
    root = Path(project_root).resolve()
    state, _ = _assert_active_change_request(root, change_request_id)
    directory = _reference_root(root, change_request_id)
    history = _load_history(directory)
    previous = next((item for item in reversed(history) if item.get("status") == "ACTIVE"), None)
    if previous is None:
        raise ProjectStateError("REFERENCE_CR_ACTIVE_BINDING_MISSING")
    record = deepcopy(previous)
    record["binding_id"] = _next_binding_id(directory)
    record["status"] = "REVOKED"
    record["supersedes"] = previous["binding_id"]
    record["created_at"] = _now()
    record["approved_change_scope"] = list(record["approved_change_scope"]) + [f"revoke:{reason.strip()}"]
    errors = validate_reference_change_binding(record, project_id=str(state["project_id"]))
    if errors:
        raise ProjectStateError("REFERENCE_CR_BINDING_INVALID:" + ",".join(errors))
    path = directory / f"reference-binding-{int(record['binding_id'].split('-')[1]):04d}.yaml"
    _write_once(path, record)
    record["artifact_ref"] = path.relative_to(root).as_posix()
    return record


def list_reference_change_bindings(project_root: str | Path, change_request_id: str) -> list[dict[str, Any]]:
    root = Path(project_root).resolve()
    return _load_history(_reference_root(root, change_request_id))


__all__ = [
    "list_reference_change_bindings",
    "register_reference_change_request",
    "revoke_reference_change_binding",
    "validate_reference_change_binding",
]
