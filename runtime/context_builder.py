"""F13：按角色、预算和批准边界构建最小上下文。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .errors import RuntimeValidationError
from .session_store import SessionStore


_FORBIDDEN_FOR_GENERATOR = ("memory/proposals/", "artifacts/design_previews/")


def _safe(root: Path, reference: str) -> Path:
    path = Path(reference.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeValidationError("上下文工件路径非法")
    resolved = (root / path).resolve()
    try: resolved.relative_to(root)
    except ValueError as exc: raise RuntimeValidationError("上下文工件路径逃逸") from exc
    if not resolved.is_file(): raise RuntimeValidationError("上下文工件不存在")
    return resolved


def build_context(session_id: str, role: str, checkpoint_id: str | None,
                  relevant_artifacts: Iterable[str], relevant_issue_ids: Iterable[str],
                  token_budget: int, *, project_root: str | Path, store: SessionStore) -> dict[str, object]:
    """返回受预算限制的原始工件片段和可验证 Event 摘要。"""
    if role not in {"planner", "generator", "evaluator"} or token_budget <= 0:
        raise RuntimeValidationError("角色或 token_budget 无效")
    root = Path(project_root).resolve(); remaining = token_budget * 4; artifacts = []
    for reference in relevant_artifacts:
        if role == "generator" and reference.replace("\\", "/").startswith(_FORBIDDEN_FOR_GENERATOR):
            raise RuntimeValidationError("Generator 不得把未批准方案或设计预览作为执行上下文")
        path = _safe(root, reference); raw = path.read_text(encoding="utf-8", errors="replace")
        fragment = raw[:remaining]; remaining -= len(fragment.encode("utf-8")); artifacts.append({"reference": reference, "content": fragment, "truncated": len(fragment) < len(raw)})
        if remaining <= 0: break
    events = [{"sequence": item.sequence, "type": item.event_type, "payload_hash": item.payload_hash} for item in store.list_events(session_id)[-20:]]
    return {"session_id": session_id, "role": role, "checkpoint_id": checkpoint_id,
            "issue_ids": list(relevant_issue_ids), "artifacts": artifacts, "events": events,
            "remaining_bytes": max(remaining, 0)}
