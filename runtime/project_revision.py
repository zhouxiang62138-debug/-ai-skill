"""project.yaml v7 Runtime 投影与 Compare-And-Swap。"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from scripts.project_state import (
    ProjectStateError,
    load_project_state,
    serialize_project_state,
    validate_project_state,
    _write_runtime_project_state_atomic,
)

from .errors import RecoveryError, StateConflictError
from .event_types import ActorType, EventType
from .leases import LeaseManager
from .policy import assert_field_ownership
from .session_store import SessionStore, stable_id, utc_now


def project_state_hash(state: dict[str, Any]) -> str:
    """计算业务状态的稳定 SHA-256。"""

    return hashlib.sha256(serialize_project_state(state).encode("utf-8")).hexdigest()


def runtime_projection(state: dict[str, Any]) -> dict[str, Any]:
    """读取并验证 v7 Runtime 投影。"""

    runtime = state.get("runtime")
    if state.get("schema_version") != 7 or not isinstance(runtime, dict):
        raise ProjectStateError("CAS 仅允许显式迁移后的 project schema v7")
    return runtime


class ProjectStateCAS:
    """用 Lease fencing 和 revision/hash 防止静默覆盖。"""

    def __init__(self, store: SessionStore, leases: LeaseManager) -> None:
        self.store = store
        self.leases = leases

    def commit(
        self,
        project_yaml: str | Path,
        next_state: dict[str, Any],
        *,
        session_id: str,
        worker_id: str,
        actor_role: str,
        lease_version: int,
        lease_token: str,
        expected_revision: int,
        idempotency_key: str,
        fail_at: str | None = None,
    ) -> dict[str, Any]:
        """提交一个 revision；失败注入仅供恢复测试使用。"""

        path = Path(project_yaml).resolve()
        current = load_project_state(path)
        current_runtime = runtime_projection(current)
        before_hash = project_state_hash(current)
        connection = self.store.raw_connection()
        try:
            existing = connection.execute(
                "SELECT * FROM state_revisions WHERE session_id=? AND idempotency_key=?",
                (session_id, idempotency_key),
            ).fetchone()
        finally:
            connection.close()
        if existing is not None and existing["status"] == "COMMITTED":
            if (
                int(existing["new_revision"]) != int(current_runtime["revision"])
                or str(existing["after_hash"]) != before_hash
            ):
                raise StateConflictError("幂等提交与当前 project.yaml 不一致")
            return {"result": "IDEMPOTENT", "revision": existing["new_revision"], "state_hash": existing["after_hash"]}
        self.leases.assert_valid(session_id, worker_id, lease_version, lease_token)
        if (
            current_runtime["session_id"] != session_id
            or current_runtime["revision"] != expected_revision
        ):
            self._conflict(
                session_id, idempotency_key, expected_revision, current_runtime["revision"]
            )
            raise StateConflictError("project.yaml revision 与 expected_revision 不一致")
        request = self.store.append_event(
            session_id,
            EventType.PROJECT_STATE_COMMIT_REQUESTED,
            ActorType.WORKER,
            worker_id,
            idempotency_key=f"{idempotency_key}:requested",
            correlation_id=idempotency_key,
            payload={
                "expected_revision": expected_revision,
                "before_hash": before_hash,
            },
        )
        prepared = copy.deepcopy(next_state)
        prepared["schema_version"] = 7
        prepared["runtime"] = copy.deepcopy(current_runtime)
        # 角色只提交其声明字段；Runtime 投影由 Commit Coordinator 独占。
        assert_field_ownership(actor_role, current, prepared)
        prepared["runtime"]["revision"] = expected_revision + 1
        after_hash = project_state_hash(prepared)
        revision_id = stable_id("revision", session_id, expected_revision + 1)
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM state_revisions
                WHERE session_id = ? AND idempotency_key = ?
                """,
                (session_id, idempotency_key),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO state_revisions VALUES
                    (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, NULL)
                    """,
                    (
                        revision_id,
                        session_id,
                        expected_revision,
                        expected_revision + 1,
                        before_hash,
                        after_hash,
                        idempotency_key,
                        request.event_id,
                        utc_now(),
                    ),
                )
            elif (
                existing["before_hash"] != before_hash
                or existing["after_hash"] != after_hash
            ):
                raise StateConflictError("幂等 revision 的内容发生变化")
        if fail_at == "before_project_state_commit":
            raise OSError("注入 project.yaml 提交前崩溃")
        # 写入前再次读取，关闭读取/生成之间的竞争窗口。
        reloaded = load_project_state(path)
        if (
            runtime_projection(reloaded)["revision"] != expected_revision
            or project_state_hash(reloaded) != before_hash
        ):
            self._conflict(
                session_id, idempotency_key, expected_revision,
                runtime_projection(reloaded)["revision"],
            )
            raise StateConflictError("写入前复核发现 project.yaml 已变化")
        # 在最后一次原子替换前重新 fencing，防止 Lease 在计算候选状态后失效。
        self.leases.assert_valid(session_id, worker_id, lease_version, lease_token)
        errors = validate_project_state(prepared, path.parent)
        if errors:
            raise ProjectStateError("CAS 候选状态无效：" + "; ".join(errors))
        _write_runtime_project_state_atomic(path, prepared)
        if fail_at == "after_project_state_commit":
            raise OSError("注入 project.yaml 提交后崩溃")
        committed = self.store.append_event(
            session_id,
            EventType.PROJECT_STATE_COMMITTED,
            ActorType.WORKER,
            worker_id,
            idempotency_key=f"{idempotency_key}:committed",
            correlation_id=idempotency_key,
            caused_by_event_id=request.event_id,
            payload={
                "revision": expected_revision + 1,
                "state_hash": after_hash,
            },
        )
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE state_revisions
                SET status='COMMITTED', committed_at=?
                WHERE revision_id=?
                """,
                (utc_now(), revision_id),
            )
        return {
            "result": "COMMITTED",
            "revision": expected_revision + 1,
            "state_hash": after_hash,
            "event_id": committed.event_id,
        }

    def _conflict(
        self,
        session_id: str,
        idempotency_key: str,
        expected_revision: int,
        actual_revision: int,
    ) -> None:
        self.store.append_event(
            session_id,
            EventType.PROJECT_STATE_CONFLICT,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key=f"{idempotency_key}:conflict:{actual_revision}",
            correlation_id=idempotency_key,
            payload={
                "expected_revision": expected_revision,
                "actual_revision": actual_revision,
            },
        )

    def recover_pending(self, project_yaml: str | Path, session_id: str) -> list[str]:
        """根据 YAML revision/hash 幂等完成或取消 pending revision。"""

        path = Path(project_yaml).resolve()
        state = load_project_state(path)
        current_revision = runtime_projection(state)["revision"]
        current_hash = project_state_hash(state)
        actions: list[str] = []
        with self.store.transaction(immediate=True) as connection:
            rows = connection.execute(
                """
                SELECT * FROM state_revisions
                WHERE session_id=? AND status='PENDING'
                ORDER BY new_revision
                """,
                (session_id,),
            ).fetchall()
            for row in rows:
                if (
                    row["new_revision"] == current_revision
                    and row["after_hash"] == current_hash
                ):
                    connection.execute(
                        """
                        UPDATE state_revisions
                        SET status='COMMITTED', committed_at=?
                        WHERE revision_id=?
                        """,
                        (utc_now(), row["revision_id"]),
                    )
                    actions.append(f"committed:{row['revision_id']}")
                elif (
                    row["expected_revision"] == current_revision
                    and row["before_hash"] == current_hash
                ):
                    connection.execute(
                        "UPDATE state_revisions SET status='ABORTED' WHERE revision_id=?",
                        (row["revision_id"],),
                    )
                    actions.append(f"aborted:{row['revision_id']}")
                else:
                    raise RecoveryError("pending revision 与项目状态无法自动对齐")
        for action in actions:
            self.store.append_event(
                session_id,
                EventType.RECOVERY_COMPLETED,
                ActorType.ORCHESTRATOR,
                "orchestrator",
                idempotency_key=f"recover:{action}",
                correlation_id=session_id,
                payload={"action": action},
            )
        return actions
