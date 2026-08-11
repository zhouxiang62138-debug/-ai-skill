"""基于 sqlite3 的持久化 Session Store。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from .errors import RuntimeStorageError, RuntimeValidationError
from .event_types import ActorType, EventType
from .attestation import attestation_hash, validate_verifier_results
from .models import Checkpoint, Event, Lease, Session
from .runtime_config import load_runtime_config


RUNTIME_SCHEMA_VERSION = 3
_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|secret|password)"
)


def utc_now() -> str:
    """返回带 UTC 时区的 ISO-8601 时间。"""

    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    """根据确定性输入生成稳定 ID。"""

    raw = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:24]}"


def canonical_json(value: Any) -> str:
    """生成可重复 hash 的 JSON。"""

    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeValidationError("Payload 必须是可安全序列化的 JSON") from exc


class SessionStore:
    """管理 Session、Event、Checkpoint、Lease 和恢复修订。"""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {load_runtime_config()['busy_timeout_ms']}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """打开显式 SQLite 事务。"""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS runtime_schema (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            project_root TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_event_sequence >= 0),
            last_checkpoint_id TEXT,
            active_worker_id TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            sequence INTEGER NOT NULL CHECK(sequence > 0),
            timestamp TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            caused_by_event_id TEXT,
            correlation_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            UNIQUE(session_id, sequence),
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TRIGGER IF NOT EXISTS events_no_update
        BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS events_no_delete
        BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            event_sequence INTEGER NOT NULL,
            project_revision INTEGER NOT NULL,
            project_state_hash TEXT NOT NULL,
            active_role TEXT,
            active_module TEXT,
            status TEXT NOT NULL,
            next_role TEXT,
            open_transaction_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, event_sequence, project_revision)
        );
        CREATE TABLE IF NOT EXISTS leases (
            session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
            worker_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            lease_version INTEGER NOT NULL CHECK(lease_version > 0),
            lease_token_hash TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS lease_fences (
            session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
            last_lease_version INTEGER NOT NULL CHECK(last_lease_version >= 0)
        );
        CREATE TABLE IF NOT EXISTS tool_calls (
            tool_call_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            request_json TEXT NOT NULL,
            result_reference TEXT,
            result_hash TEXT,
            payload_hash TEXT NOT NULL,
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS tool_attempts (
            attempt_id TEXT PRIMARY KEY,
            tool_call_id TEXT NOT NULL REFERENCES tool_calls(tool_call_id),
            input_hash TEXT NOT NULL,
            code_snapshot_hash TEXT NOT NULL,
            environment_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            result_reference TEXT,
            result_hash TEXT,
            UNIQUE(tool_call_id, input_hash, code_snapshot_hash, environment_hash)
        );
        CREATE TABLE IF NOT EXISTS state_revisions (
            revision_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            expected_revision INTEGER NOT NULL,
            new_revision INTEGER NOT NULL,
            before_hash TEXT NOT NULL,
            after_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            caused_by_event_id TEXT,
            created_at TEXT NOT NULL,
            committed_at TEXT,
            UNIQUE(session_id, new_revision),
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS state_revision_attempts (
            revision_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            expected_revision INTEGER NOT NULL,
            new_revision INTEGER NOT NULL,
            before_hash TEXT NOT NULL,
            after_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            caused_by_event_id TEXT,
            created_at TEXT NOT NULL,
            committed_at TEXT,
            UNIQUE(session_id, idempotency_key)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS active_state_revision_per_revision
        ON state_revision_attempts(session_id, new_revision)
        WHERE status IN ('PENDING', 'COMMITTED');
        CREATE TABLE IF NOT EXISTS role_runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            worker_id TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            result_json TEXT
        );
        CREATE TABLE IF NOT EXISTS model_invocations (
            invocation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            run_id TEXT NOT NULL REFERENCES role_runs(run_id),
            role TEXT NOT NULL,
            invocation_sequence INTEGER NOT NULL CHECK(invocation_sequence > 0),
            status TEXT NOT NULL,
            previous_invocation_id TEXT,
            context_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            handoff_id TEXT,
            idempotency_key TEXT NOT NULL,
            UNIQUE(session_id, invocation_sequence),
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS rollover_handoffs (
            handoff_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            invocation_id TEXT NOT NULL REFERENCES model_invocations(invocation_id),
            role TEXT NOT NULL,
            reason_json TEXT NOT NULL,
            handoff_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TRIGGER IF NOT EXISTS rollover_handoffs_no_update
        BEFORE UPDATE ON rollover_handoffs BEGIN SELECT RAISE(ABORT, 'rollover handoffs are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS rollover_handoffs_no_delete
        BEFORE DELETE ON rollover_handoffs BEGIN SELECT RAISE(ABORT, 'rollover handoffs are append-only'); END;
        CREATE TABLE IF NOT EXISTS context_manifests (
            context_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            project_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            role TEXT NOT NULL,
            workflow_state TEXT NOT NULL,
            project_revision INTEGER NOT NULL,
            project_state_hash TEXT NOT NULL,
            context_hash TEXT NOT NULL,
            context_policy_hash TEXT NOT NULL,
            budget_fingerprint TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            omitted_sources_json TEXT NOT NULL,
            complete INTEGER NOT NULL CHECK(complete IN (0, 1)),
            created_at TEXT NOT NULL,
            persisted_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS phase_attestations (
            attestation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            run_id TEXT NOT NULL REFERENCES role_runs(run_id),
            role TEXT NOT NULL,
            project_revision INTEGER NOT NULL CHECK(project_revision >= 0),
            context_id TEXT NOT NULL REFERENCES context_manifests(context_id),
            invocation_id TEXT NOT NULL REFERENCES model_invocations(invocation_id),
            required_steps_hash TEXT NOT NULL,
            verifier_results_json TEXT NOT NULL,
            evidence_refs_json TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            attestation_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(session_id, idempotency_key)
        );
        CREATE TRIGGER IF NOT EXISTS phase_attestations_no_update
        BEFORE UPDATE ON phase_attestations BEGIN SELECT RAISE(ABORT, 'phase attestations are append-only'); END;
        CREATE TRIGGER IF NOT EXISTS phase_attestations_no_delete
        BEFORE DELETE ON phase_attestations BEGIN SELECT RAISE(ABORT, 'phase attestations are append-only'); END;
        """
        connection = self._connect()
        try:
            connection.executescript(ddl)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(leases)")
            }
            if "lease_token_hash" not in columns:
                connection.execute(
                    "ALTER TABLE leases ADD COLUMN lease_token_hash TEXT NOT NULL DEFAULT ''"
                )
            tool_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tool_calls)")}
            for name in ("started_at", "result_hash"):
                if name not in tool_columns:
                    connection.execute(f"ALTER TABLE tool_calls ADD COLUMN {name} TEXT")
            # v3 保留旧表及全部历史记录，并把它们复制到支持多次 ABORTED 尝试的新表。
            # 旧表不删除，避免为了迁移破坏恢复审计链。
            connection.execute(
                """
                INSERT OR IGNORE INTO state_revision_attempts(
                    revision_id, session_id, expected_revision, new_revision,
                    before_hash, after_hash, status, idempotency_key,
                    caused_by_event_id, created_at, committed_at
                )
                SELECT revision_id, session_id, expected_revision, new_revision,
                       before_hash, after_hash, status, idempotency_key,
                       caused_by_event_id, created_at, committed_at
                FROM state_revisions
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO runtime_schema(version, applied_at) VALUES (?, ?)",
                (RUNTIME_SCHEMA_VERSION, utc_now()),
            )
            connection.commit()
        finally:
            connection.close()

    def create_session(
        self,
        project_id: str,
        project_root: str | Path,
        *,
        idempotency_key: str,
        created_at: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        """创建 Session；同一幂等键返回同一个 Session。"""

        if not project_id or not idempotency_key:
            raise RuntimeValidationError("project_id 和 idempotency_key 不能为空")
        root = str(Path(project_root).resolve())
        timestamp = created_at or utc_now()
        resolved_session_id = session_id or stable_id(
            "session", project_id, root, idempotency_key
        )
        if not re.fullmatch(r"session-[a-f0-9]{24}", resolved_session_id):
            raise RuntimeValidationError("session_id 格式无效")
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    session_id, project_id, project_root, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (resolved_session_id, project_id, root, timestamp, timestamp),
            )
        session = self.get_session(resolved_session_id)
        self.append_event(
            resolved_session_id,
            EventType.SESSION_CREATED,
            ActorType.ORCHESTRATOR,
            "orchestrator",
            idempotency_key=f"session-created:{idempotency_key}",
            correlation_id=resolved_session_id,
            payload={"project_id": project_id, "project_root": root},
            timestamp=timestamp,
        )
        return self.get_session(resolved_session_id)

    def get_session(self, session_id: str) -> Session:
        """读取 Session。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError(f"Session 不存在：{session_id}")
        return Session(**dict(row))

    def set_session_status(self, session_id: str, status: str) -> None:
        """更新 Session 运行状态。"""

        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
                (status, utc_now(), session_id),
            ).rowcount
            if changed != 1:
                raise RuntimeStorageError(f"Session 不存在：{session_id}")

    def append_event(
        self,
        session_id: str,
        event_type: EventType | str,
        actor_type: ActorType | str,
        actor_id: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        payload: dict[str, Any],
        caused_by_event_id: str | None = None,
        timestamp: str | None = None,
    ) -> Event:
        """在事务中分配 sequence 并追加不可变事件。"""

        with self.transaction(immediate=True) as connection:
            return self._append_event_in_transaction(
                connection,
                session_id,
                event_type,
                actor_type,
                actor_id,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                payload=payload,
                caused_by_event_id=caused_by_event_id,
                timestamp=timestamp,
            )

    def _append_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        event_type: EventType | str,
        actor_type: ActorType | str,
        actor_id: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        payload: dict[str, Any],
        caused_by_event_id: str | None = None,
        timestamp: str | None = None,
    ) -> Event:
        """在调用方已有的 SQLite 事务内追加事件。"""

        event_value = EventType(event_type).value
        actor_value = ActorType(actor_type).value
        if not actor_id or not idempotency_key or not correlation_id:
            raise RuntimeValidationError("事件 actor、幂等键和 correlation_id 不能为空")
        payload_text = canonical_json(payload)
        if len(payload_text.encode("utf-8")) > load_runtime_config()["payload_limit_bytes"]:
            raise RuntimeValidationError("事件 Payload 超过大小上限")
        if _SECRET_PATTERN.search(payload_text):
            raise RuntimeValidationError("事件 Payload 包含疑似敏感字段")
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        existing = connection.execute(
            "SELECT * FROM events WHERE session_id = ? AND idempotency_key = ?",
            (session_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["event_type"] != event_value or existing["payload_hash"] != payload_hash:
                raise RuntimeValidationError("幂等键已关联不同事件内容")
            return self._event_from_row(existing)
        session = connection.execute(
            "SELECT last_event_sequence FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session is None:
            raise RuntimeStorageError(f"Session 不存在：{session_id}")
        sequence = int(session["last_event_sequence"]) + 1
        event_id = stable_id("event", session_id, sequence)
        event_timestamp = timestamp or utc_now()
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, session_id, sequence, event_timestamp, actor_value, actor_id,
             event_value, caused_by_event_id, correlation_id, idempotency_key,
             payload_text, payload_hash),
        )
        connection.execute(
            "UPDATE sessions SET last_event_sequence = ?, updated_at = ? WHERE session_id = ?",
            (sequence, event_timestamp, session_id),
        )
        row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return self._event_from_row(row)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        return Event(**value)

    def list_events(self, session_id: str) -> list[Event]:
        """按 sequence 返回 Session 事件。"""

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY sequence",
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
        return [self._event_from_row(row) for row in rows]

    def save_context_manifest(
        self,
        *,
        context_id: str,
        session_id: str,
        project_id: str,
        run_id: str,
        role: str,
        workflow_state: str,
        project_revision: int,
        project_state_hash: str,
        context_hash: str,
        context_policy_hash: str,
        budget_fingerprint: str,
        sources: list[dict[str, Any]],
        omitted_sources: list[dict[str, Any]],
        created_at: str,
        complete: bool = True,
    ) -> dict[str, Any]:
        """通过 F10 正式 API 持久化不含正文的 Context Manifest。"""

        session = self.get_session(session_id)
        if session.project_id != project_id:
            raise RuntimeValidationError("CONTEXT_MANIFEST_PROJECT_MISMATCH")
        if not all(
            isinstance(value, str) and value
            for value in (
                context_id,
                run_id,
                role,
                workflow_state,
                project_state_hash,
                context_hash,
                context_policy_hash,
                budget_fingerprint,
                created_at,
            )
        ):
            raise RuntimeValidationError("CONTEXT_MANIFEST_INVALID")
        if not isinstance(project_revision, int) or project_revision < 0:
            raise RuntimeValidationError("CONTEXT_MANIFEST_INVALID")
        if not isinstance(complete, bool):
            raise RuntimeValidationError("CONTEXT_MANIFEST_INVALID")
        safe_sources = self._validate_context_manifest_sources(sources)
        safe_omitted = self._validate_context_manifest_sources(
            omitted_sources, omitted=True
        )
        sources_json = canonical_json(safe_sources)
        omitted_json = canonical_json(safe_omitted)
        if _SECRET_PATTERN.search(sources_json + omitted_json):
            raise RuntimeValidationError("CONTEXT_MANIFEST_SECRET_FORBIDDEN")
        persisted_at = utc_now()
        row_values = (
            context_id,
            session_id,
            project_id,
            run_id,
            role,
            workflow_state,
            project_revision,
            project_state_hash,
            context_hash,
            context_policy_hash,
            budget_fingerprint,
            sources_json,
            omitted_json,
            1 if complete else 0,
            created_at,
            persisted_at,
        )
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM context_manifests WHERE context_id=?",
                (context_id,),
            ).fetchone()
            if existing is not None:
                existing_valid = True
                try:
                    self._context_manifest_from_row(existing)
                except RuntimeStorageError:
                    existing_valid = False
                if not existing_valid or int(existing["complete"]) != 1:
                    connection.execute(
                        """
                        UPDATE context_manifests SET
                            session_id=?, project_id=?, run_id=?, role=?,
                            workflow_state=?, project_revision=?, project_state_hash=?,
                            context_hash=?, context_policy_hash=?, budget_fingerprint=?,
                            sources_json=?, omitted_sources_json=?, complete=?,
                            created_at=?, persisted_at=?
                        WHERE context_id=?
                        """,
                        (*row_values[1:], context_id),
                    )
                    row = connection.execute(
                        "SELECT * FROM context_manifests WHERE context_id=?",
                        (context_id,),
                    ).fetchone()
                    return self._context_manifest_from_row(row)
                existing_dict = dict(existing)
                if tuple(existing_dict[key] for key in (
                    "session_id",
                    "project_id",
                    "run_id",
                    "role",
                    "workflow_state",
                    "project_revision",
                    "project_state_hash",
                    "context_hash",
                    "context_policy_hash",
                    "budget_fingerprint",
                    "sources_json",
                    "omitted_sources_json",
                    "complete",
                    "created_at",
                )) != (
                    session_id,
                    project_id,
                    run_id,
                    role,
                    workflow_state,
                    project_revision,
                    project_state_hash,
                    context_hash,
                    context_policy_hash,
                    budget_fingerprint,
                    sources_json,
                    omitted_json,
                    1 if complete else 0,
                    created_at,
                ):
                    raise RuntimeValidationError("CONTEXT_MANIFEST_CONFLICT")
                return self._context_manifest_from_row(existing)
            connection.execute(
                """
                INSERT INTO context_manifests(
                    context_id, session_id, project_id, run_id, role,
                    workflow_state, project_revision, project_state_hash,
                    context_hash, context_policy_hash, budget_fingerprint,
                    sources_json, omitted_sources_json, complete, created_at,
                    persisted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row_values,
            )
            row = connection.execute(
                "SELECT * FROM context_manifests WHERE context_id=?",
                (context_id,),
            ).fetchone()
        return self._context_manifest_from_row(row)

    @staticmethod
    def _validate_context_manifest_sources(
        sources: list[dict[str, Any]], *, omitted: bool = False
    ) -> list[dict[str, Any]]:
        if not isinstance(sources, list):
            raise RuntimeValidationError("CONTEXT_MANIFEST_INVALID")
        allowed = (
            {
                "reference",
                "content_hash",
                "reason",
                "priority",
                "omission_reason",
                "size",
            }
            if omitted
            else {
                "source_type",
                "reference",
                "content_hash",
                "reason",
                "priority",
                "delivery_mode",
                "size",
                "original_size",
                "included_size",
                "is_excerpt",
            }
        )
        result: list[dict[str, Any]] = []
        for source in sources:
            if not isinstance(source, dict) or not set(source).issubset(allowed):
                raise RuntimeValidationError("CONTEXT_MANIFEST_INVALID")
            if "content" in source:
                raise RuntimeValidationError("CONTEXT_MANIFEST_CONTENT_FORBIDDEN")
            result.append(dict(source))
        return result

    @staticmethod
    def _context_manifest_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise RuntimeStorageError("CONTEXT_MANIFEST_MISSING")
        value = dict(row)
        try:
            value["sources"] = json.loads(value.pop("sources_json"))
            value["omitted_sources"] = json.loads(value.pop("omitted_sources_json"))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeStorageError("CONTEXT_MANIFEST_INVALID") from exc
        if not isinstance(value["sources"], list) or not isinstance(
            value["omitted_sources"], list
        ):
            raise RuntimeStorageError("CONTEXT_MANIFEST_INVALID")
        return value

    def get_context_manifest(
        self, session_id: str, context_id: str
    ) -> dict[str, Any]:
        """读取同一 Session 的 durable Context Manifest。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM context_manifests WHERE session_id=? AND context_id=?",
                (session_id, context_id),
            ).fetchone()
        finally:
            connection.close()
        return self._context_manifest_from_row(row)

    def find_previous_context_manifest(
        self,
        session_id: str,
        *,
        project_id: str,
        role: str,
        workflow_state: str | None = None,
    ) -> dict[str, Any] | None:
        """只查同一 Session/Project/Role 的已完成 Manifest。"""

        session = self.get_session(session_id)
        if session.project_id != project_id:
            raise RuntimeValidationError("CONTEXT_MANIFEST_PROJECT_MISMATCH")
        query = """
            SELECT * FROM context_manifests
            WHERE session_id=? AND project_id=? AND role=? AND complete=1
        """
        parameters: list[Any] = [session_id, project_id, role]
        if workflow_state is not None:
            query += " AND workflow_state=?"
            parameters.append(workflow_state)
        query += " ORDER BY persisted_at DESC, context_id DESC LIMIT 1"
        connection = self._connect()
        try:
            row = connection.execute(query, parameters).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return self._context_manifest_from_row(row)

    def create_checkpoint(
        self,
        session_id: str,
        *,
        project_revision: int,
        project_state_hash: str,
        active_role: str | None,
        active_module: str | None,
        status: str,
        next_role: str | None,
        open_transaction_ids: list[str] | tuple[str, ...],
    ) -> Checkpoint:
        """为当前事件位置创建幂等 Checkpoint。"""

        with self.transaction(immediate=True) as connection:
            session = connection.execute(
                "SELECT last_event_sequence FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise RuntimeStorageError(f"Session 不存在：{session_id}")
            sequence = int(session["last_event_sequence"])
            checkpoint_id = stable_id(
                "checkpoint", session_id, sequence, project_revision, project_state_hash
            )
            created_at = utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    session_id,
                    sequence,
                    project_revision,
                    project_state_hash,
                    active_role,
                    active_module,
                    status,
                    next_role,
                    canonical_json(list(open_transaction_ids)),
                    created_at,
                ),
            )
            connection.execute(
                """
                UPDATE sessions SET last_checkpoint_id = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (checkpoint_id, created_at, session_id),
            )
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        value = dict(row)
        value["open_transaction_ids"] = tuple(
            json.loads(value.pop("open_transaction_ids_json"))
        )
        return Checkpoint(**value)

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        """读取 Checkpoint。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError(f"Checkpoint 不存在：{checkpoint_id}")
        value = dict(row)
        value["open_transaction_ids"] = tuple(
            json.loads(value.pop("open_transaction_ids_json"))
        )
        return Checkpoint(**value)

    def raw_connection(self) -> sqlite3.Connection:
        """仅供验证与迁移工具使用的受控连接。"""

        return self._connect()

    def request_tool_call(
        self,
        session_id: str,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        idempotency_key: str,
    ) -> str:
        """持久化工具请求；重复请求返回同一个 tool_call_id。"""

        request = {"tool_name": tool_name, "arguments": arguments}
        request_json = canonical_json(request)
        if _SECRET_PATTERN.search(request_json):
            raise RuntimeValidationError("工具请求包含疑似敏感字段")
        payload_hash = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
        tool_call_id = stable_id("tool", session_id, idempotency_key)
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT * FROM tool_calls
                WHERE session_id=? AND idempotency_key=?
                """,
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["payload_hash"] != payload_hash:
                    raise RuntimeValidationError("工具幂等键已关联不同请求")
                return str(existing["tool_call_id"])
            connection.execute(
                """
                INSERT INTO tool_calls(
                    tool_call_id, session_id, idempotency_key, status, requested_at,
                    completed_at, request_json, result_reference, payload_hash
                ) VALUES (?, ?, ?, 'REQUESTED', ?, NULL, ?, NULL, ?)
                """,
                (
                    tool_call_id,
                    session_id,
                    idempotency_key,
                    utc_now(),
                    request_json,
                    payload_hash,
                ),
            )
            self._append_event_in_transaction(
                connection, session_id, EventType.TOOL_CALL_REQUESTED, ActorType.TOOL, tool_name,
                idempotency_key=f"tool-requested:{idempotency_key}", correlation_id=tool_call_id,
                payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
            )
        return tool_call_id

    def start_tool_call(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        code_snapshot_hash: str = "unknown",
        environment_hash: str = "unknown",
    ) -> str:
        """将 REQUESTED 原子推进为 STARTED，并在同一事务追加 Event。"""

        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT status FROM tool_calls WHERE tool_call_id=? AND session_id=?", (tool_call_id, session_id)).fetchone()
            if row is None:
                raise RuntimeStorageError(f"Tool Call 不存在：{tool_call_id}")
            input_hash = connection.execute(
                "SELECT payload_hash FROM tool_calls WHERE tool_call_id=?", (tool_call_id,)
            ).fetchone()["payload_hash"]
            attempt_id = stable_id(
                "attempt", tool_call_id, input_hash, code_snapshot_hash, environment_hash
            )
            existing = connection.execute(
                "SELECT status FROM tool_attempts WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if existing is not None:
                return attempt_id
            if row["status"] == "STARTED":
                raise RuntimeValidationError("TOOL_CALL_ALREADY_STARTED")
            if row["status"] not in {"REQUESTED", "SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}:
                raise RuntimeValidationError("TOOL_CALL_INVALID_TRANSITION")
            connection.execute("UPDATE tool_calls SET status='STARTED', started_at=? WHERE tool_call_id=?", (utc_now(), tool_call_id))
            connection.execute(
                """INSERT INTO tool_attempts(
                    attempt_id, tool_call_id, input_hash, code_snapshot_hash,
                    environment_hash, status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'STARTED', ?)""",
                (attempt_id, tool_call_id, input_hash, code_snapshot_hash, environment_hash, utc_now()),
            )
            self._append_event_in_transaction(
                connection, session_id, EventType.TOOL_CALL_STARTED, ActorType.TOOL, "tool-runtime",
                idempotency_key=f"tool-started:{attempt_id}", correlation_id=tool_call_id,
                payload={"tool_call_id": tool_call_id, "attempt_id": attempt_id},
            )
        return attempt_id

    def get_tool_call(self, session_id: str, tool_call_id: str) -> dict[str, Any]:
        """读取 Tool Call 状态；不暴露给 ExecutionEnvironment。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM tool_calls WHERE tool_call_id=? AND session_id=?",
                (tool_call_id, session_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError(f"Tool Call 不存在：{tool_call_id}")
        return dict(row)

    def complete_tool_call(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        result_reference: str,
        status: str = "SUCCEEDED",
        result_hash: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        """幂等记录工具完成；结果正文保存在外部受控引用中。"""

        if status not in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}:
            raise RuntimeValidationError("TOOL_CALL_INVALID_TERMINAL_STATUS")
        if not result_hash:
            raise RuntimeValidationError("TOOL_RESULT_HASH_REQUIRED")
        try:
            self.read_tool_result(result_reference, result_hash)
        except Exception as exc:
            raise RuntimeValidationError("TOOL_RESULT_UNVERIFIABLE") from exc

        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM tool_calls WHERE tool_call_id=? AND session_id=?",
                (tool_call_id, session_id),
            ).fetchone()
            if row is None:
                raise RuntimeStorageError(f"Tool Call 不存在：{tool_call_id}")
            if row["status"] in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}:
                if row["result_reference"] != result_reference or row["status"] != status:
                    raise RuntimeValidationError("工具完成重放的结果引用不一致")
                return
            if row["status"] != "STARTED":
                raise RuntimeValidationError("TOOL_CALL_INVALID_TRANSITION")
            connection.execute(
                """
                UPDATE tool_calls
                SET status=?, completed_at=?, result_reference=?, result_hash=?
                WHERE tool_call_id=?
                """,
                (status, utc_now(), result_reference, result_hash, tool_call_id),
            )
            resolved_attempt = attempt_id or connection.execute(
                "SELECT attempt_id FROM tool_attempts WHERE tool_call_id=? AND status='STARTED'",
                (tool_call_id,),
            ).fetchone()
            if resolved_attempt is not None:
                value = resolved_attempt if isinstance(resolved_attempt, str) else resolved_attempt["attempt_id"]
                connection.execute(
                    """UPDATE tool_attempts SET status=?, finished_at=?, result_reference=?, result_hash=?
                    WHERE attempt_id=?""",
                    (status, utc_now(), result_reference, result_hash, value),
                )
            event_attempt_id = attempt_id
            if event_attempt_id is None:
                attempt_row = connection.execute(
                    "SELECT attempt_id FROM tool_attempts WHERE tool_call_id=? ORDER BY started_at DESC LIMIT 1",
                    (tool_call_id,),
                ).fetchone()
                event_attempt_id = None if attempt_row is None else str(attempt_row["attempt_id"])
            self._append_event_in_transaction(
                connection,
                session_id,
                EventType.TOOL_CALL_COMPLETED
                if status == "SUCCEEDED"
                else EventType.TOOL_CALL_TIMED_OUT
                if status == "TIMED_OUT"
                else EventType.TOOL_CALL_FAILED,
                ActorType.TOOL,
                "tool-runtime",
                idempotency_key=f"tool-completed:{event_attempt_id or tool_call_id}", correlation_id=tool_call_id,
                payload={"tool_call_id": tool_call_id, "result_reference": result_reference,
                         "status": status, "result_hash": result_hash, "attempt_id": attempt_id},
            )

    def completed_tool_calls(self, session_id: str) -> list[dict[str, Any]]:
        """返回可供崩溃恢复重放引用的已完成工具调用。"""

        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM tool_calls
                WHERE session_id=? AND status='SUCCEEDED'
                ORDER BY requested_at
                """,
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def create_role_run(self, session_id: str, worker_id: str, role: str) -> str:
        """创建一次可审计角色运行，并记录 ROLE_STARTED。"""

        run_id = f"run-{uuid.uuid4().hex}"
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO role_runs(run_id, session_id, worker_id, role, status, created_at) VALUES (?, ?, ?, ?, 'STARTED', ?)",
                (run_id, session_id, worker_id, role, utc_now()),
            )
        self.append_event(session_id, EventType.ROLE_STARTED, ActorType.WORKER, worker_id, idempotency_key=f"role-started:{run_id}", correlation_id=run_id, payload={"run_id": run_id, "role": role})
        return run_id

    def get_role_run(self, session_id: str, run_id: str) -> dict[str, Any]:
        """读取单个 Role Run；不存在时不允许猜测或新建。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM role_runs WHERE run_id=? AND session_id=?", (run_id, session_id)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("ROLE_RUN_MISSING")
        return dict(row)

    def create_model_invocation(
        self,
        session_id: str,
        run_id: str,
        role: str,
        context_id: str,
        *,
        idempotency_key: str,
        previous_invocation_id: str | None = None,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        """创建独立于 Durable Runtime Session 的模型 Invocation。"""

        if not all(isinstance(value, str) and value for value in (run_id, role, context_id, idempotency_key)):
            raise RuntimeValidationError("MODEL_INVOCATION_INVALID")
        role_run = self.get_role_run(session_id, run_id)
        if role_run["role"] != role:
            raise RuntimeValidationError("MODEL_INVOCATION_ROLE_MISMATCH")
        if previous_invocation_id is not None and not previous_invocation_id:
            raise RuntimeValidationError("MODEL_INVOCATION_PREVIOUS_INVALID")
        invocation_id = stable_id("model-invocation", session_id, run_id, idempotency_key)
        timestamp = started_at or utc_now()
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM model_invocations WHERE session_id=? AND idempotency_key=?",
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if any(
                    existing[key] != value
                    for key, value in {
                        "run_id": run_id,
                        "role": role,
                        "context_id": context_id,
                        "previous_invocation_id": previous_invocation_id,
                    }.items()
                ):
                    raise RuntimeValidationError("MODEL_INVOCATION_IDEMPOTENCY_CONFLICT")
                return dict(existing)
            if previous_invocation_id is not None:
                previous = connection.execute(
                    "SELECT status, session_id, role FROM model_invocations WHERE invocation_id=?",
                    (previous_invocation_id,),
                ).fetchone()
                if previous is None or previous["session_id"] != session_id or previous["role"] != role:
                    raise RuntimeValidationError("MODEL_INVOCATION_PREVIOUS_INVALID")
                if previous["status"] != "ROLLED_OVER":
                    raise RuntimeValidationError("MODEL_INVOCATION_PREVIOUS_NOT_ROLLED_OVER")
            sequence_row = connection.execute(
                "SELECT COALESCE(MAX(invocation_sequence), 0) + 1 AS next_sequence FROM model_invocations WHERE session_id=?",
                (session_id,),
            ).fetchone()
            sequence = int(sequence_row["next_sequence"])
            connection.execute(
                """
                INSERT INTO model_invocations(
                    invocation_id, session_id, run_id, role, invocation_sequence,
                    status, previous_invocation_id, context_id, started_at,
                    ended_at, handoff_id, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, NULL, NULL, ?)
                """,
                (invocation_id, session_id, run_id, role, sequence,
                 previous_invocation_id, context_id, timestamp, idempotency_key),
            )
            self._append_event_in_transaction(
                connection,
                session_id,
                EventType.MODEL_INVOCATION_STARTED,
                ActorType.ORCHESTRATOR,
                "context-runtime",
                idempotency_key=f"model-invocation-started:{invocation_id}",
                correlation_id=invocation_id,
                payload={
                    "invocation_id": invocation_id,
                    "run_id": run_id,
                    "role": role,
                    "previous_invocation_id": previous_invocation_id,
                    "context_id": context_id,
                },
                timestamp=timestamp,
            )
            row = connection.execute(
                "SELECT * FROM model_invocations WHERE invocation_id=?", (invocation_id,)
            ).fetchone()
        return dict(row)

    def get_model_invocation(self, session_id: str, invocation_id: str) -> dict[str, Any]:
        """读取同一 Runtime Session 的模型 Invocation。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM model_invocations WHERE session_id=? AND invocation_id=?",
                (session_id, invocation_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("MODEL_INVOCATION_MISSING")
        return dict(row)

    def create_phase_attestation(
        self,
        *,
        session_id: str,
        run_id: str,
        role: str,
        project_revision: int,
        context_id: str,
        invocation_id: str,
        required_steps_hash: str,
        verifier_results: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """由 Runtime 创建追加式 Phase Attestation，不接受模型提供的 ID 或摘要。"""

        if not all(
            isinstance(value, str) and value
            for value in (
                session_id,
                run_id,
                role,
                context_id,
                invocation_id,
                required_steps_hash,
                idempotency_key,
            )
        ) or not isinstance(project_revision, int) or project_revision < 0:
            raise RuntimeValidationError("ATTESTATION_INVALID")
        role_run = self.get_role_run(session_id, run_id)
        invocation = self.get_model_invocation(session_id, invocation_id)
        if role_run["role"] != role or invocation["run_id"] != run_id or invocation["role"] != role:
            raise RuntimeValidationError("ATTESTATION_RUN_ROLE_MISMATCH")
        if invocation["context_id"] != context_id:
            raise RuntimeValidationError("ATTESTATION_CONTEXT_MISMATCH")
        context = self.get_context_manifest(session_id, context_id)
        if context["session_id"] != session_id or int(context["project_revision"]) != project_revision:
            raise RuntimeValidationError("ATTESTATION_REVISION_MISMATCH")
        safe_results, evidence_refs = validate_verifier_results(verifier_results)
        results_json = canonical_json(safe_results)
        refs_json = canonical_json(evidence_refs)
        if _SECRET_PATTERN.search(results_json + refs_json):
            raise RuntimeValidationError("ATTESTATION_SECRET_FORBIDDEN")
        fields = {
            "session_id": session_id,
            "run_id": run_id,
            "role": role,
            "project_revision": project_revision,
            "context_id": context_id,
            "invocation_id": invocation_id,
            "required_steps_hash": required_steps_hash,
            "verifier_results": safe_results,
            "evidence_refs": evidence_refs,
            "idempotency_key": idempotency_key,
        }
        digest = attestation_hash(fields)
        attestation_id = stable_id("phase-attestation", session_id, run_id, idempotency_key)
        timestamp = utc_now()
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM phase_attestations WHERE session_id=? AND idempotency_key=?",
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["attestation_hash"] != digest:
                    raise RuntimeValidationError("ATTESTATION_IDEMPOTENCY_CONFLICT")
                return self._phase_attestation_from_row(existing)
            connection.execute(
                """
                INSERT INTO phase_attestations(
                    attestation_id, session_id, run_id, role, project_revision,
                    context_id, invocation_id, required_steps_hash,
                    verifier_results_json, evidence_refs_json, idempotency_key,
                    attestation_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attestation_id,
                    session_id,
                    run_id,
                    role,
                    project_revision,
                    context_id,
                    invocation_id,
                    required_steps_hash,
                    results_json,
                    refs_json,
                    idempotency_key,
                    digest,
                    timestamp,
                ),
            )
            self._append_event_in_transaction(
                connection,
                session_id,
                EventType.PHASE_ATTESTATION_CREATED,
                ActorType.ORCHESTRATOR,
                "phase-runtime",
                idempotency_key=f"phase-attestation-created:{attestation_id}",
                correlation_id=run_id,
                payload={
                    "attestation_id": attestation_id,
                    "run_id": run_id,
                    "role": role,
                    "project_revision": project_revision,
                    "context_id": context_id,
                    "invocation_id": invocation_id,
                    "required_steps_hash": required_steps_hash,
                    "evidence_ref_count": len(evidence_refs),
                },
                timestamp=timestamp,
            )
            row = connection.execute(
                "SELECT * FROM phase_attestations WHERE attestation_id=?",
                (attestation_id,),
            ).fetchone()
        return self._phase_attestation_from_row(row)

    @staticmethod
    def _phase_attestation_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise RuntimeStorageError("ATTESTATION_MISSING")
        value = dict(row)
        value["verifier_results"] = json.loads(value.pop("verifier_results_json"))
        value["evidence_refs"] = json.loads(value.pop("evidence_refs_json"))
        return value

    def get_phase_attestation(self, session_id: str, attestation_id: str) -> dict[str, Any]:
        """读取同一 Session 的不可变 Attestation。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM phase_attestations WHERE session_id=? AND attestation_id=?",
                (session_id, attestation_id),
            ).fetchone()
        finally:
            connection.close()
        return self._phase_attestation_from_row(row)

    def get_state_revision_by_idempotency(self, session_id: str, idempotency_key: str) -> dict[str, Any] | None:
        """查询提交幂等记录，供重复提交在 Role Run 已完成后安全重放。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM state_revision_attempts WHERE session_id=? AND idempotency_key=?",
                (session_id, idempotency_key),
            ).fetchone()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    def active_model_invocations(self, session_id: str) -> list[dict[str, Any]]:
        """列出崩溃恢复时仍停留在 ACTIVE 的 Invocation。"""

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM model_invocations WHERE session_id=? AND status='ACTIVE' ORDER BY invocation_sequence",
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def recover_interrupted_model_invocations(self, session_id: str) -> list[str]:
        """将崩溃留下的 ACTIVE Invocation 以固定摘要标记失败；可重复执行。"""

        ids = [str(item["invocation_id"]) for item in self.active_model_invocations(session_id)]
        for invocation_id in ids:
            self.complete_model_invocation(
                session_id,
                invocation_id,
                status="FAILED",
                result_hash=hashlib.sha256(
                    f"interrupted-after-crash:{invocation_id}".encode("utf-8")
                ).hexdigest(),
            )
        return ids

    def complete_model_invocation(
        self,
        session_id: str,
        invocation_id: str,
        *,
        status: str = "SUCCEEDED",
        result_hash: str,
    ) -> dict[str, Any]:
        """以摘要结束 Invocation；事件不保存模型输出正文或凭据。"""

        if status not in {"SUCCEEDED", "FAILED"} or not isinstance(result_hash, str) or not result_hash:
            raise RuntimeValidationError("MODEL_INVOCATION_RESULT_INVALID")
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM model_invocations WHERE session_id=? AND invocation_id=?",
                (session_id, invocation_id),
            ).fetchone()
            if row is None:
                raise RuntimeStorageError("MODEL_INVOCATION_MISSING")
            if row["status"] in {"SUCCEEDED", "FAILED"}:
                if row["status"] != status:
                    raise RuntimeValidationError("MODEL_INVOCATION_TERMINAL_CONFLICT")
                return dict(row)
            if row["status"] != "ACTIVE":
                raise RuntimeValidationError("MODEL_INVOCATION_INVALID_TRANSITION")
            ended_at = utc_now()
            connection.execute(
                "UPDATE model_invocations SET status=?, ended_at=? WHERE session_id=? AND invocation_id=?",
                (status, ended_at, session_id, invocation_id),
            )
            self._append_event_in_transaction(
                connection,
                session_id,
                EventType.MODEL_INVOCATION_COMPLETED
                if status == "SUCCEEDED"
                else EventType.MODEL_INVOCATION_FAILED,
                ActorType.ORCHESTRATOR,
                "phase-runtime",
                idempotency_key=f"model-invocation-{status.lower()}:{invocation_id}",
                correlation_id=invocation_id,
                payload={
                    "invocation_id": invocation_id,
                    "status": status,
                    "result_hash": result_hash,
                },
                timestamp=ended_at,
            )
            result = connection.execute(
                "SELECT * FROM model_invocations WHERE session_id=? AND invocation_id=?",
                (session_id, invocation_id),
            ).fetchone()
        return dict(result)

    def append_rollover_handoff(
        self,
        session_id: str,
        invocation_id: str,
        role: str,
        handoff: dict[str, Any],
        *,
        reason: dict[str, Any],
        idempotency_key: str,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """追加 Rollover Handoff，并原子地结束旧 Invocation。"""

        if not isinstance(handoff, dict) or not isinstance(reason, dict) or not idempotency_key:
            raise RuntimeValidationError("ROLLOVER_HANDOFF_INVALID")
        handoff_json = canonical_json(handoff)
        reason_json = canonical_json(reason)
        if len(handoff_json.encode("utf-8")) > 32 * 1024 or len(reason_json.encode("utf-8")) > 4096:
            raise RuntimeValidationError("ROLLOVER_HANDOFF_TOO_LARGE")
        if _SECRET_PATTERN.search(handoff_json + reason_json):
            raise RuntimeValidationError("ROLLOVER_HANDOFF_SECRET_FORBIDDEN")
        timestamp = created_at or utc_now()
        handoff_id = stable_id("rollover-handoff", session_id, invocation_id, idempotency_key)
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM rollover_handoffs WHERE session_id=? AND idempotency_key=?",
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["handoff_json"] != handoff_json or existing["reason_json"] != reason_json:
                    raise RuntimeValidationError("ROLLOVER_HANDOFF_IDEMPOTENCY_CONFLICT")
                return self._rollover_handoff_from_row(existing)
            invocation = connection.execute(
                "SELECT * FROM model_invocations WHERE session_id=? AND invocation_id=?",
                (session_id, invocation_id),
            ).fetchone()
            if invocation is None:
                raise RuntimeStorageError("MODEL_INVOCATION_MISSING")
            if invocation["role"] != role or invocation["status"] != "ACTIVE":
                raise RuntimeValidationError("MODEL_INVOCATION_ROLLOVER_INVALID")
            connection.execute(
                """
                INSERT INTO rollover_handoffs(
                    handoff_id, session_id, invocation_id, role, reason_json,
                    handoff_json, created_at, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (handoff_id, session_id, invocation_id, role, reason_json,
                 handoff_json, timestamp, idempotency_key),
            )
            connection.execute(
                """
                UPDATE model_invocations
                SET status='ROLLED_OVER', ended_at=?, handoff_id=?
                WHERE session_id=? AND invocation_id=?
                """,
                (timestamp, handoff_id, session_id, invocation_id),
            )
            reason_codes = reason.get("reason_codes", [])
            if not isinstance(reason_codes, list) or any(not isinstance(item, str) for item in reason_codes):
                raise RuntimeValidationError("ROLLOVER_REASON_INVALID")
            self._append_event_in_transaction(
                connection,
                session_id,
                EventType.MODEL_INVOCATION_ROLLED_OVER,
                ActorType.ORCHESTRATOR,
                "context-runtime",
                idempotency_key=f"model-invocation-rollover:{handoff_id}",
                correlation_id=invocation_id,
                payload={
                    "handoff_id": handoff_id,
                    "invocation_id": invocation_id,
                    "reason_codes": reason_codes,
                    "reference_count": len(handoff.get("references", [])) if isinstance(handoff.get("references", []), list) else 0,
                },
                timestamp=timestamp,
            )
            row = connection.execute(
                "SELECT * FROM rollover_handoffs WHERE handoff_id=?", (handoff_id,)
            ).fetchone()
        return self._rollover_handoff_from_row(row)

    @staticmethod
    def _rollover_handoff_from_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise RuntimeStorageError("ROLLOVER_HANDOFF_MISSING")
        value = dict(row)
        try:
            value["reason"] = json.loads(value.pop("reason_json"))
            value["handoff"] = json.loads(value.pop("handoff_json"))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeStorageError("ROLLOVER_HANDOFF_INVALID") from exc
        return value

    def get_rollover_handoff(self, session_id: str, handoff_id: str) -> dict[str, Any]:
        """读取同一 Session 的结构化 Rollover Handoff。"""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM rollover_handoffs WHERE session_id=? AND handoff_id=?",
                (session_id, handoff_id),
            ).fetchone()
        finally:
            connection.close()
        return self._rollover_handoff_from_row(row)

    def complete_role_run(self, session_id: str, run_id: str, result: dict[str, Any]) -> None:
        """不可重复地完成 Role Run 并追加 ROLE_COMPLETED。"""

        text = canonical_json(result)
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT status FROM role_runs WHERE run_id=? AND session_id=?", (run_id, session_id)).fetchone()
            if row is None:
                raise RuntimeStorageError("ROLE_RUN_MISSING")
            if row["status"] == "COMPLETED":
                return
            if row["status"] != "STARTED":
                raise RuntimeValidationError("ROLE_RUN_INVALID_TRANSITION")
            connection.execute("UPDATE role_runs SET status='COMPLETED', completed_at=?, result_json=? WHERE run_id=?", (utc_now(), text, run_id))
        self.append_event(session_id, EventType.ROLE_COMPLETED, ActorType.WORKER, "worker", idempotency_key=f"role-completed:{run_id}", correlation_id=run_id, payload={"run_id": run_id})

    def fail_role_run(self, session_id: str, run_id: str, reason: dict[str, Any]) -> None:
        """持久化角色失败；失败路径绝不写入完成状态。"""

        text = canonical_json(reason)
        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT status, worker_id FROM role_runs WHERE run_id=? AND session_id=?", (run_id, session_id)).fetchone()
            if row is None:
                raise RuntimeStorageError("ROLE_RUN_MISSING")
            if row["status"] == "FAILED":
                return
            if row["status"] != "STARTED":
                raise RuntimeValidationError("ROLE_RUN_INVALID_TRANSITION")
            connection.execute("UPDATE role_runs SET status='FAILED', completed_at=?, result_json=? WHERE run_id=?", (utc_now(), text, run_id))
            worker_id = str(row["worker_id"])
        self.append_event(session_id, EventType.ROLE_FAILED, ActorType.WORKER, worker_id, idempotency_key=f"role-failed:{run_id}", correlation_id=run_id, payload={"run_id": run_id})

    def recover_interrupted_tool_calls(self, session_id: str) -> list[str]:
        """将进程崩溃时未完成的 STARTED 调用显式标为未知，供人工或安全重放决策。"""

        with self.transaction(immediate=True) as connection:
            rows = connection.execute(
                "SELECT tool_call_id FROM tool_calls WHERE session_id=? AND status='STARTED'",
                (session_id,),
            ).fetchall()
            ids = [str(row["tool_call_id"]) for row in rows]
            for tool_call_id in ids:
                connection.execute(
                    "UPDATE tool_calls SET status='UNKNOWN_AFTER_CRASH', completed_at=? WHERE tool_call_id=?",
                    (utc_now(), tool_call_id),
                )
                self._append_event_in_transaction(
                connection, session_id, EventType.TOOL_CALL_FAILED, ActorType.TOOL, "tool-runtime",
                idempotency_key=f"tool-unknown-after-crash:{tool_call_id}",
                correlation_id=tool_call_id,
                payload={"tool_call_id": tool_call_id, "status": "UNKNOWN_AFTER_CRASH"},
            )
        return ids

    def recover_requested_tool_calls(self, session_id: str) -> list[str]:
        """列出尚未开始的持久化请求；调用方可安全重放同一请求。"""

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT tool_call_id FROM tool_calls WHERE session_id=? AND status='REQUESTED' ORDER BY requested_at",
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
        return [str(row["tool_call_id"]) for row in rows]

    def write_tool_result(self, tool_call_id: str, result: dict[str, Any]) -> tuple[str, str]:
        """将结果以独占文件写入 Control Plane，返回相对引用和 SHA-256。"""

        payload = canonical_json(result).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.path.parent / "tool-results" / tool_call_id
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"attempt-{uuid.uuid4().hex}.json"
        target = directory / filename
        try:
            with target.open("xb") as handle:
                handle.write(payload)
        except FileExistsError as exc:  # pragma: no cover - uuid 冲突防御
            raise RuntimeStorageError("TOOL_RESULT_PATH_COLLISION") from exc
        return str(target.relative_to(self.path.parent).as_posix()), digest

    def read_tool_result(self, reference: str, expected_hash: str) -> dict[str, Any]:
        """读取并校验不可变结果；篡改即阻止恢复。"""

        candidate = (self.path.parent / reference).resolve()
        root = (self.path.parent / "tool-results").resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise RuntimeStorageError("TOOL_RESULT_REFERENCE_INVALID") from exc
        raw = candidate.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected_hash:
            raise RuntimeStorageError("TOOL_RESULT_HASH_MISMATCH")
        return json.loads(raw.decode("utf-8"))
