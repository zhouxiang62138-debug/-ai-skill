"""Stage 7 Runtime Candidate 选择与 Snapshot 恢复服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from runtime.errors import RuntimeValidationError
from scripts.best_candidate import best_validated_candidate, load_candidate


class SnapshotRestorer(Protocol):
    """既有 Workspace Snapshot Service 的最小恢复接口。"""

    def restore(self, context: Any, snapshot_id: str) -> None:
        ...


class CandidateRuntimeService:
    """Evaluator 只能推荐；真正恢复由 Runtime 通过 Snapshot Service 执行。"""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    def best(self) -> dict[str, Any] | None:
        return best_validated_candidate(self.project_root)

    def restore(
        self,
        context: Any,
        snapshot_service: SnapshotRestorer,
        *,
        candidate_id: str,
    ) -> dict[str, Any]:
        candidate = load_candidate(self.project_root, candidate_id)
        if not hasattr(snapshot_service, "restore"):
            raise RuntimeValidationError("CANDIDATE_SNAPSHOT_SERVICE_INVALID")
        snapshot_service.restore(context, candidate["snapshot_id"])
        return candidate


__all__ = ["CandidateRuntimeService", "SnapshotRestorer"]
