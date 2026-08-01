"""不含业务决策的确定性 Orchestrator。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.project_state import (
    ProjectStateError,
    load_project_state,
    validate_project_state,
)
from scripts.evaluation_protocol import recover_evaluation_transaction

from .errors import RuntimeValidationError
from .control_plane import require_session_database, control_plane_id
from .event_types import ActorType, EventType
from .leases import LeaseManager
from .project_revision import ProjectStateCAS, project_state_hash, runtime_projection
from .recovery import RecoveryManager
from .role_selector import Selection, select_role
from .session_store import SessionStore


class Orchestrator:
    """定位项目、管理 Lease、选角、Checkpoint 和恢复。"""

    def __init__(
        self, project_root: str | Path, *, control_plane_home: str | Path | None = None
    ) -> None:
        self.root = Path(project_root).resolve()
        self.project_yaml = self.root / "project.yaml"
        if not self.project_yaml.is_file():
            raise ProjectStateError("项目根目录缺少 project.yaml")
        state = load_project_state(self.project_yaml)
        projection = runtime_projection(state)
        project_id = str(state["project_id"])
        if projection.get("control_plane_id") != control_plane_id(project_id):
            raise RuntimeValidationError("RUNTIME_BINDING_MISMATCH")
        # v7 已绑定项目只能打开既有控制平面历史；构造器绝不创建新 DB。
        self.store = SessionStore(
            require_session_database(project_id, home=control_plane_home)
        )
        self.leases = LeaseManager(self.store)
        self.cas = ProjectStateCAS(self.store, self.leases)
        self.recovery = RecoveryManager(self.store, self.cas)

    def start(self, *, worker_id: str | None = None) -> dict[str, Any]:
        """注册/恢复 v7 Session 并返回确定性角色运行请求。"""

        state = self._validated_state()
        worker_id = worker_id or f"worker-{uuid4()}"
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
        run_id = (
            self.store.create_role_run(session.session_id, worker_id, selection.target)
            if selection.kind == "ROLE" and selection.target is not None
            else None
        )
        return {
            "session_id": session.session_id,
            "worker_id": worker_id,
            "lease_version": lease.lease_version,
            "lease_token": lease.lease_token,
            "selection": selection,
            "event_id": event.event_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "run_id": run_id,
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

    def commit_step(self, session_id: str, run_id: str, lease_token: str, result: dict[str, Any]) -> dict[str, Any]:
        """校验持久化 Run、Lease 与结构化结果后通过 CAS 提交角色步骤。"""

        run = self.store.get_role_run(session_id, run_id)
        if run["status"] != "STARTED":
            raise RuntimeValidationError("ROLE_RUN_INVALID_TRANSITION")
        required = {"source_status", "target_status", "changed_fields", "expected_revision", "idempotency_key"}
        if set(result) != required or not isinstance(result["changed_fields"], dict):
            raise RuntimeValidationError("STEP_RESULT_INVALID")
        lease = self.leases.get(session_id)
        if lease.worker_id != run["worker_id"]:
            raise RuntimeValidationError("ROLE_RUN_WORKER_MISMATCH")
        committed = self.cas.commit_patch(
            self.project_yaml, result["changed_fields"],
            source_status=str(result["source_status"]), target_status=str(result["target_status"]),
            session_id=session_id,
            worker_id=str(run["worker_id"]), actor_role=str(run["role"]),
            lease_version=lease.lease_version, lease_token=lease_token,
            expected_revision=int(result["expected_revision"]),
            idempotency_key=str(result["idempotency_key"]),
        )
        self.store.complete_role_run(session_id, run_id, committed)
        self.leases.release(session_id, str(run["worker_id"]), lease.lease_version, lease_token)
        return committed

    def fail_step(self, session_id: str, run_id: str, reason: dict[str, Any]) -> None:
        """持久化失败 Run；不提交候选 project state。"""

        self.store.fail_role_run(session_id, run_id, reason)

    def pause(self, session_id: str) -> None:
        """暂停 Session，不改变业务状态。"""

        self.store.set_session_status(session_id, "PAUSED")
        self.leases.revoke_for_lifecycle(session_id, reason="session-paused")
        self.store.append_event(
            session_id,
            EventType.SESSION_PAUSED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key="session-paused",
            correlation_id=session_id,
            payload={},
        )

    def resume(self, session_id: str, *, worker_id: str | None = None) -> dict[str, Any]:
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
        return self.start(worker_id=worker_id)

    def recover_session(
        self, session_id: str, *, worker_id: str | None = None
    ) -> dict[str, Any]:
        """取得 Lease 后恢复 Runtime 与 Evaluation 事务。"""

        worker_id = worker_id or f"worker-{uuid4()}"
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
                    actor_role="evaluator",
                    lease_version=lease.lease_version,
                    lease_token=lease.lease_token or "",
                    expected_revision=expected,
                    idempotency_key=f"evaluation-recovery:{evaluation_id}",
                )

            recover_evaluation_transaction(
                root, evaluation_id, state_writer=writer
            )

        try:
            return self.recovery.recover(
                session_id, evaluation_recoverer=recover_evaluation
            )
        finally:
            self.leases.revoke_for_lifecycle(session_id, reason="recovery-finished")

    def _validated_state(self) -> dict[str, Any]:
        state = load_project_state(self.project_yaml)
        errors = validate_project_state(state, self.root)
        if errors:
            raise RuntimeValidationError("project.yaml 无效：" + "; ".join(errors))
        return state
