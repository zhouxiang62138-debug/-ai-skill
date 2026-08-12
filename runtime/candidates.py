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
        return best_validated_candidate(self.project_root, require_profile_bound=True)

    def restore(
        self,
        context: Any,
        snapshot_service: SnapshotRestorer,
        *,
        candidate_id: str,
        evaluation_profile: str | None = None,
        evaluation_profile_hash: str | None = None,
        rubric_version: str | None = None,
        calibration_suite_version: str | None = None,
        evaluator_model: str | None = None,
        profile: dict[str, Any] | None = None,
        legacy_read_only: bool = False,
    ) -> dict[str, Any]:
        """按 Candidate 绑定的 Profile 重新验证后才允许 Runtime 恢复。"""

        candidate = load_candidate(
            self.project_root,
            candidate_id,
            evaluation_profile=evaluation_profile,
            evaluation_profile_hash=evaluation_profile_hash,
            rubric_version=rubric_version,
            calibration_suite_version=calibration_suite_version,
            evaluator_model=evaluator_model,
            profile=profile,
            legacy_read_only=legacy_read_only,
        )
        if candidate.get("schema_version") == 2 and not evaluator_model:
            raise RuntimeValidationError("CANDIDATE_EVALUATOR_MODEL_BINDING_REQUIRED")
        if not hasattr(snapshot_service, "restore"):
            raise RuntimeValidationError("CANDIDATE_SNAPSHOT_SERVICE_INVALID")
        snapshot_service.restore(context, candidate["snapshot_id"])
        expected_workspace_hash = candidate.get("workspace_hash")
        if expected_workspace_hash is not None:
            checker = getattr(snapshot_service, "workspace_hash", None)
            actual_workspace_hash = checker(context, candidate["snapshot_id"]) if callable(checker) else getattr(context, "workspace_hash", None)
            if actual_workspace_hash != expected_workspace_hash:
                raise RuntimeValidationError("CANDIDATE_WORKSPACE_HASH_MISMATCH")
        return candidate


__all__ = ["CandidateRuntimeService", "SnapshotRestorer"]
