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
from typing import Any, Iterator

from .errors import RuntimeStorageError, RuntimeValidationError
from .event_types import ActorType, EventType, MAX_EVENT_PAYLOAD_BYTES
from .models import Checkpoint, Event, Lease, Session


RUNTIME_SCHEMA_VERSION = 1
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
        connection.execute("PRAGMA busy_timeout = 5000")
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

        event_value = EventType(event_type).value
        actor_value = ActorType(actor_type).value
        if not actor_id or not idempotency_key or not correlation_id:
            raise RuntimeValidationError("事件 actor、幂等键和 correlation_id 不能为空")
        payload_text = canonical_json(payload)
        if len(payload_text.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise RuntimeValidationError("事件 Payload 超过大小上限")
        if _SECRET_PATTERN.search(payload_text):
            raise RuntimeValidationError("事件 Payload 包含疑似敏感字段")
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with self.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM events WHERE session_id = ? AND idempotency_key = ?",
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != event_value
                    or existing["payload_hash"] != payload_hash
                ):
                    raise RuntimeValidationError("幂等键已关联不同事件内容")
                return self._event_from_row(existing)
            session = connection.execute(
                "SELECT last_event_sequence FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if session is None:
                raise RuntimeStorageError(f"Session 不存在：{session_id}")
            sequence = int(session["last_event_sequence"]) + 1
            event_id = stable_id("event", session_id, sequence)
            event_timestamp = timestamp or utc_now()
            connection.execute(
                """
                INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    sequence,
                    event_timestamp,
                    actor_value,
                    actor_id,
                    event_value,
                    caused_by_event_id,
                    correlation_id,
                    idempotency_key,
                    payload_text,
                    payload_hash,
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET last_event_sequence = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (sequence, event_timestamp, session_id),
            )
            row = connection.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
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
        self.append_event(
            session_id,
            EventType.TOOL_CALL_REQUESTED,
            ActorType.TOOL,
            tool_name,
            idempotency_key=f"tool-requested:{idempotency_key}",
            correlation_id=tool_call_id,
            payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
        )
        return tool_call_id

    def start_tool_call(self, session_id: str, tool_call_id: str) -> None:
        """将 REQUESTED 原子推进为 STARTED，并在同一事务追加 Event。"""

        with self.transaction(immediate=True) as connection:
            row = connection.execute("SELECT status FROM tool_calls WHERE tool_call_id=? AND session_id=?", (tool_call_id, session_id)).fetchone()
            if row is None:
                raise RuntimeStorageError(f"Tool Call 不存在：{tool_call_id}")
            if row["status"] == "STARTED":
                return
            if row["status"] != "REQUESTED":
                raise RuntimeValidationError("TOOL_CALL_INVALID_TRANSITION")
            connection.execute("UPDATE tool_calls SET status='STARTED', started_at=? WHERE tool_call_id=?", (utc_now(), tool_call_id))
        self.append_event(session_id, EventType.TOOL_CALL_STARTED, ActorType.TOOL, "tool-runtime", idempotency_key=f"tool-started:{tool_call_id}", correlation_id=tool_call_id, payload={"tool_call_id": tool_call_id})

    def complete_tool_call(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        result_reference: str,
        status: str = "SUCCEEDED",
        result_hash: str | None = None,
    ) -> None:
        """幂等记录工具完成；结果正文保存在外部受控引用中。"""

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
            if status not in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}:
                raise RuntimeValidationError("TOOL_CALL_INVALID_TERMINAL_STATUS")
            connection.execute(
                """
                UPDATE tool_calls
                SET status=?, completed_at=?, result_reference=?, result_hash=?
                WHERE tool_call_id=?
                """,
                (status, utc_now(), result_reference, result_hash, tool_call_id),
            )
        self.append_event(
            session_id,
            EventType.TOOL_CALL_COMPLETED if status == "SUCCEEDED" else EventType.TOOL_CALL_FAILED,
            ActorType.TOOL,
            "tool-runtime",
            idempotency_key=f"tool-completed:{tool_call_id}",
            correlation_id=tool_call_id,
            payload={
                "tool_call_id": tool_call_id,
                "result_reference": result_reference, "status": status, "result_hash": result_hash,
            },
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
        for tool_call_id in ids:
            self.append_event(
                session_id, EventType.TOOL_CALL_FAILED, ActorType.TOOL, "tool-runtime",
                idempotency_key=f"tool-unknown-after-crash:{tool_call_id}",
                correlation_id=tool_call_id,
                payload={"tool_call_id": tool_call_id, "status": "UNKNOWN_AFTER_CRASH"},
            )
        return ids

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
