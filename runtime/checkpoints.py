"""Checkpoint 的薄封装，保持 Orchestrator 依赖清晰。"""

from .models import Checkpoint
from .session_store import SessionStore


def create_checkpoint(store: SessionStore, session_id: str, **facts: object) -> Checkpoint:
    """使用结构化事实创建 Checkpoint。"""

    return store.create_checkpoint(session_id, **facts)  # type: ignore[arg-type]
