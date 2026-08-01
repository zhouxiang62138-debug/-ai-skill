"""不含业务决策的确定性 Orchestrator。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.project_state import (
    ProjectStateError,
    load_project_state,
    validate_project_state,
)
from scripts.evaluation_protocol import recover_evaluation_transaction

from .errors import RuntimeValidationError
from .event_types import ActorType, EventType
from .leases import LeaseManager
from .project_revision import ProjectStateCAS, project_state_hash, runtime_projection
from .recovery import RecoveryManager
from .role_selector import Selection, select_role
from .session_store import SessionStore


class Orchestrator:
    """定位项目、管理 Lease、选角、Checkpoint 和恢复。"""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root).resolve()
        self.project_yaml = self.root / "project.yaml"
        if not self.project_yaml.is_file():
            raise ProjectStateError("项目根目录缺少 project.yaml")
        self.store = SessionStore(self.root / ".runtime" / "sessions.sqlite3")
        self.leases = LeaseManager(self.store)
        self.cas = ProjectStateCAS(self.store, self.leases)
        self.recovery = RecoveryManager(self.store, self.cas)

    def start(self, *, worker_id: str = "worker-main") -> dict[str, Any]:
        """注册/恢复 v7 Session 并返回确定性角色运行请求。"""

        state = self._validated_state()
        projection = runtime_projection(state)
        session = self.store.create_session(
            str(state["project_id"]),
            self.root,
            idempotency_key=f"project-runtime:{projection['session_id']}",
            session_id=str(projection["session_id"]),
        )
        lease = self.leases.acquire(session.session_id, worker_id)
        selection = select_role(state)
        event = self.store.append_event(
            session.session_id,
            EventType.ROLE_SELECTED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key=(
                f"role-selected:{projection['revision']}:{selection.kind}:"
                f"{selection.target or 'wait'}"
            ),
            correlation_id=session.session_id,
            payload={
                "kind": selection.kind,
                "target": selection.target,
                "reason": selection.reason,
            },
        )
        checkpoint = self.store.create_checkpoint(
            session.session_id,
            project_revision=int(projection["revision"]),
            project_state_hash=project_state_hash(state),
            active_role=selection.target if selection.kind == "ROLE" else None,
            active_module=selection.target if selection.kind == "MODULE" else None,
            status=str(state["status"]),
            next_role=state.get("next_role"),
            open_transaction_ids=[],
        )
        return {
            "session_id": session.session_id,
            "worker_id": worker_id,
            "lease_version": lease.lease_version,
            "selection": selection,
            "event_id": event.event_id,
            "checkpoint_id": checkpoint.checkpoint_id,
        }

    def inspect(self, session_id: str) -> dict[str, Any]:
        """只读返回 Session、事件计数、Checkpoint 和业务状态。"""

        session = self.store.get_session(session_id)
        state = self._validated_state()
        return {
            "session": session,
            "event_count": len(self.store.list_events(session_id)),
            "checkpoint": (
                self.store.get_checkpoint(session.last_checkpoint_id)
                if session.last_checkpoint_id
                else None
            ),
            "project_runtime": runtime_projection(state),
            "selection": select_role(state),
        }

    def pause(self, session_id: str) -> None:
        """暂停 Session，不改变业务状态。"""

        self.store.set_session_status(session_id, "PAUSED")
        self.store.append_event(
            session_id,
            EventType.SESSION_PAUSED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key="session-paused",
            correlation_id=session_id,
            payload={},
        )

    def resume(self, session_id: str) -> dict[str, Any]:
        """恢复 Session 并重新从持久化状态选角。"""

        self.store.set_session_status(session_id, "ACTIVE")
        self.store.append_event(
            session_id,
            EventType.SESSION_RESUMED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key=f"session-resumed:{len(self.store.list_events(session_id))}",
            correlation_id=session_id,
            payload={},
        )
        return self.inspect(session_id)

    def recover_session(
        self, session_id: str, *, worker_id: str = "worker-recovery"
    ) -> dict[str, Any]:
        """取得 Lease 后恢复 Runtime 与 Evaluation 事务。"""

        try:
            lease = self.leases.acquire(session_id, worker_id)
        except Exception:
            expired = {
                item.session_id: item
                for item in self.leases.detect_expired()
            }
            if session_id not in expired:
                raise
            lease = self.leases.steal_expired(session_id, worker_id)

        def recover_evaluation(root: Path, evaluation_id: str) -> Any:
            def writer(path: str | Path, candidate: dict[str, Any]) -> None:
                current = load_project_state(path)
                expected = int(runtime_projection(current)["revision"])
                self.cas.commit(
                    path,
                    candidate,
                    session_id=session_id,
                    worker_id=worker_id,
                    lease_version=lease.lease_version,
                    expected_revision=expected,
                    idempotency_key=f"evaluation-recovery:{evaluation_id}",
                )

            recover_evaluation_transaction(
                root, evaluation_id, state_writer=writer
            )

        return self.recovery.recover(
            session_id, evaluation_recoverer=recover_evaluation
        )

    def _validated_state(self) -> dict[str, Any]:
        state = load_project_state(self.project_yaml)
        errors = validate_project_state(state, self.root)
        if errors:
            raise RuntimeValidationError("project.yaml 无效：" + "; ".join(errors))
        return state
