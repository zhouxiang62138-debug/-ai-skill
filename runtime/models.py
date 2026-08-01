"""运行时持久化数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Session:
    session_id: str
    project_id: str
    project_root: str
    status: str
    created_at: str
    updated_at: str
    last_event_sequence: int
    last_checkpoint_id: str | None
    active_worker_id: str | None


@dataclass(frozen=True)
class Event:
    event_id: str
    session_id: str
    sequence: int
    timestamp: str
    actor_type: str
    actor_id: str
    event_type: str
    caused_by_event_id: str | None
    correlation_id: str
    idempotency_key: str
    payload: dict[str, Any]
    payload_hash: str


@dataclass(frozen=True)
class Lease:
    session_id: str
    worker_id: str
    acquired_at: str
    expires_at: str
    heartbeat_at: str
    lease_version: int
    # 仅在 acquire/renew 的返回值中出现；数据库永不保存明文。
    lease_token: str | None = None


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    session_id: str
    event_sequence: int
    project_revision: int
    project_state_hash: str
    active_role: str | None
    active_module: str | None
    status: str
    next_role: str | None
    open_transaction_ids: tuple[str, ...]
    created_at: str
