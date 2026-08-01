"""基于 SQLite 事务和版本栅栏的 Worker Lease。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from .errors import LeaseError, RuntimeStorageError
from .event_types import ActorType, EventType
from .models import Lease
from .session_store import SessionStore, utc_now


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise LeaseError("Lease 时间必须带时区")
    return parsed.astimezone(timezone.utc)


class LeaseManager:
    """维护每个 Session 唯一的主 Worker Lease。"""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def acquire(
        self,
        session_id: str,
        worker_id: str,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> Lease:
        """获取无主或已过期 Lease；未过期时拒绝抢占。"""

        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if ttl_seconds <= 0 or not worker_id:
            raise LeaseError("Lease TTL 和 worker_id 无效")
        expires = instant + timedelta(seconds=ttl_seconds)
        with self.store.transaction(immediate=True) as connection:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone() is None:
                raise RuntimeStorageError(f"Session 不存在：{session_id}")
            current = connection.execute(
                "SELECT * FROM leases WHERE session_id = ?", (session_id,)
            ).fetchone()
            if current is not None and _parse(current["expires_at"]) > instant:
                if current["worker_id"] == worker_id:
                    raise LeaseError("同一 Worker 必须持有原 Lease Token")
                raise LeaseError("Session 已被其他 Worker 持有")
            fence = connection.execute(
                "SELECT last_lease_version FROM lease_fences WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            version = int(fence["last_lease_version"]) + 1 if fence else 1
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            values = (
                session_id,
                worker_id,
                instant.isoformat(),
                expires.isoformat(),
                instant.isoformat(),
                version,
                token_hash,
            )
            connection.execute(
                """
                INSERT INTO leases VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    worker_id=excluded.worker_id,
                    acquired_at=excluded.acquired_at,
                    expires_at=excluded.expires_at,
                    heartbeat_at=excluded.heartbeat_at,
                    lease_version=excluded.lease_version,
                    lease_token_hash=excluded.lease_token_hash
                """,
                values,
            )
            connection.execute(
                """
                INSERT INTO lease_fences(session_id, last_lease_version) VALUES (?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_lease_version=excluded.last_lease_version
                """,
                (session_id, version),
            )
            connection.execute(
                "UPDATE sessions SET active_worker_id = ?, updated_at = ? WHERE session_id = ?",
                (worker_id, instant.isoformat(), session_id),
            )
        lease = self.get(session_id)
        self.store.append_event(
            session_id,
            EventType.LEASE_ACQUIRED,
            ActorType.WORKER,
            worker_id,
            idempotency_key=f"lease-acquired:{version}",
            correlation_id=session_id,
            payload={"lease_version": version, "expires_at": expires.isoformat()},
        )
        return Lease(**{**lease.__dict__, "lease_token": token})

    def renew(
        self,
        session_id: str,
        worker_id: str,
        lease_version: int,
        lease_token: str,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> Lease:
        """续约时校验 Worker、版本和未过期状态。"""

        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires = instant + timedelta(seconds=ttl_seconds)
        with self.store.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM leases WHERE session_id = ?", (session_id,)
            ).fetchone()
            if (
                current is None
                or current["worker_id"] != worker_id
                or current["lease_version"] != lease_version
                or not secrets.compare_digest(
                    str(current["lease_token_hash"]),
                    hashlib.sha256(lease_token.encode("utf-8")).hexdigest(),
                )
                or _parse(current["expires_at"]) <= instant
            ):
                raise LeaseError("Lease 已过期、换主或版本不匹配")
            connection.execute(
                """
                UPDATE leases SET expires_at = ?, heartbeat_at = ?
                WHERE session_id = ?
                """,
                (expires.isoformat(), instant.isoformat(), session_id),
            )
        self.store.append_event(
            session_id,
            EventType.LEASE_RENEWED,
            ActorType.WORKER,
            worker_id,
            idempotency_key=f"lease-renewed:{lease_version}:{instant.isoformat()}",
            correlation_id=session_id,
            payload={"lease_version": lease_version, "expires_at": expires.isoformat()},
        )
        lease = self.get(session_id)
        return Lease(**{**lease.__dict__, "lease_token": lease_token})

    def release(self, session_id: str, worker_id: str, lease_version: int, lease_token: str) -> None:
        """只允许当前版本的持有者释放 Lease。"""

        with self.store.transaction(immediate=True) as connection:
            current = connection.execute(
                "SELECT * FROM leases WHERE session_id = ?", (session_id,)
            ).fetchone()
            if (
                current is None
                or current["worker_id"] != worker_id
                or current["lease_version"] != lease_version
                or not secrets.compare_digest(str(current["lease_token_hash"]), hashlib.sha256(lease_token.encode("utf-8")).hexdigest())
            ):
                raise LeaseError("Worker 或 Lease 版本不匹配")
            connection.execute("DELETE FROM leases WHERE session_id = ?", (session_id,))
            connection.execute(
                "UPDATE sessions SET active_worker_id = NULL, updated_at = ? WHERE session_id = ?",
                (utc_now(), session_id),
            )
        self.store.append_event(
            session_id,
            EventType.LEASE_RELEASED,
            ActorType.WORKER,
            worker_id,
            idempotency_key=f"lease-released:{lease_version}",
            correlation_id=session_id,
            payload={"lease_version": lease_version},
        )

    def assert_valid(
        self,
        session_id: str,
        worker_id: str,
        lease_version: int,
        lease_token: str,
        *,
        now: datetime | None = None,
    ) -> Lease:
        """在每次状态提交前执行 Lease fencing 校验。"""

        lease = self.get(session_id)
        session = self.store.get_session(session_id)
        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if (
            session.status != "ACTIVE"
            or
            lease.worker_id != worker_id
            or lease.lease_version != lease_version
            or not secrets.compare_digest(
                self._token_hash(session_id), hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
            )
            or _parse(lease.expires_at) <= instant
        ):
            raise LeaseError("无有效 Lease，禁止提交业务状态")
        return lease

    def _token_hash(self, session_id: str) -> str:
        connection = self.store.raw_connection()
        try:
            row = connection.execute("SELECT lease_token_hash FROM leases WHERE session_id=?", (session_id,)).fetchone()
        finally:
            connection.close()
        return "" if row is None else str(row["lease_token_hash"])

    def detect_expired(
        self, *, now: datetime | None = None
    ) -> list[Lease]:
        """返回已过期 Lease，不修改状态。"""

        instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        connection = self.store.raw_connection()
        try:
            rows = connection.execute("SELECT * FROM leases").fetchall()
        finally:
            connection.close()
        return [self._lease_from_row(row) for row in rows if _parse(row["expires_at"]) <= instant]

    def steal_expired(
        self,
        session_id: str,
        worker_id: str,
        *,
        ttl_seconds: float = 30.0,
        now: datetime | None = None,
    ) -> Lease:
        """显式接管已过期 Lease。"""

        return self.acquire(
            session_id, worker_id, ttl_seconds=ttl_seconds, now=now
        )

    def get(self, session_id: str) -> Lease:
        """读取当前 Lease。"""

        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM leases WHERE session_id = ?", (session_id,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise LeaseError(f"Session 没有 Lease：{session_id}")
        return self._lease_from_row(row)

    @staticmethod
    def _lease_from_row(row: object) -> Lease:
        value = dict(row)  # type: ignore[arg-type]
        value.pop("lease_token_hash", None)
        return Lease(**value)
