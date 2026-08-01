"""F10 幂等恢复协调器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from scripts.project_state import load_project_state

from .errors import ProjectMissingError, RecoveryError
from .event_types import ActorType, EventType
from .project_revision import ProjectStateCAS, runtime_projection
from .session_store import SessionStore


class RecoveryManager:
    """恢复 pending revision、工具结果引用和 Evaluation 事务。"""

    def __init__(self, store: SessionStore, cas: ProjectStateCAS) -> None:
        self.store = store
        self.cas = cas

    def recover(
        self,
        session_id: str,
        *,
        evaluation_recoverer: Callable[[Path, str], Any] | None = None,
    ) -> dict[str, Any]:
        """执行可重复恢复；相同事实不会产生重复状态递增。"""

        session = self.store.get_session(session_id)
        root = Path(session.project_root)
        project_yaml = root / "project.yaml"
        if not root.is_dir() or not project_yaml.is_file():
            raise ProjectMissingError("Session DB 可读，但项目文件缺失")
        state = load_project_state(project_yaml)
        if runtime_projection(state)["session_id"] != session_id:
            raise RecoveryError("项目 Runtime 投影与 Session DB 不一致")
        self.store.append_event(
            session_id,
            EventType.RECOVERY_STARTED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key=f"recovery-started:{runtime_projection(state)['revision']}",
            correlation_id=session_id,
            payload={"project_revision": runtime_projection(state)["revision"]},
        )
        revision_actions = self.cas.recover_pending(project_yaml, session_id)
        interrupted_tools = self.store.recover_interrupted_tool_calls(session_id)
        completed_tools = []
        for row in self.store.completed_tool_calls(session_id):
            reference, digest = row["result_reference"], row["result_hash"]
            if not reference or not digest:
                raise RecoveryError("TOOL_RESULT_MISSING_OR_UNVERIFIABLE")
            try:
                self.store.read_tool_result(str(reference), str(digest))
            except Exception as exc:
                raise RecoveryError("TOOL_RESULT_BLOCKED") from exc
            completed_tools.append({"tool_call_id": row["tool_call_id"], "result_reference": reference})
        recovered_evaluations: list[str] = []
        transaction_root = root / "evaluation" / ".transactions"
        if transaction_root.is_dir():
            for journal_path in sorted(transaction_root.glob("*/journal.json")):
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                if journal.get("status") != "RECOVERY_REQUIRED":
                    continue
                evaluation_id = str(journal.get("evaluation_id"))
                if evaluation_recoverer is None:
                    recovered_evaluations.append(f"pending:{evaluation_id}")
                else:
                    evaluation_recoverer(root, evaluation_id)
                    recovered_evaluations.append(f"recovered:{evaluation_id}")
        result = {
            "revision_actions": revision_actions,
            "completed_tool_calls": completed_tools,
            "interrupted_tool_calls": interrupted_tools,
            "evaluation_transactions": recovered_evaluations,
        }
        self.store.append_event(
            session_id,
            EventType.RECOVERY_COMPLETED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key=(
                "recovery-completed:"
                f"{runtime_projection(load_project_state(project_yaml))['revision']}"
            ),
            correlation_id=session_id,
            payload={
                "revision_action_count": len(revision_actions),
                "tool_call_count": len(completed_tools),
                "interrupted_tool_call_count": len(interrupted_tools),
                "evaluation_transaction_count": len(recovered_evaluations),
            },
        )
        return result


def session_database_missing(
    project_root: str | Path, *, control_plane_home: str | Path | None = None
) -> bool:
    """检测已绑定项目的外部 Session Control Plane 是否缺失。"""

    root = Path(project_root).resolve()
    project_yaml = root / "project.yaml"
    if not project_yaml.is_file():
        return False
    state = load_project_state(project_yaml)
    if state.get("schema_version") != 7:
        return False
    from .control_plane import session_database_path

    return not session_database_path(
        str(state["project_id"]), home=control_plane_home
    ).is_file()
