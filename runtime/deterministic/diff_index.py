"""F14-B approved baseline 到 current revision 的确定性 Diff Index。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from ..errors import RuntimeValidationError
from ..session_store import SessionStore
from .store import DerivedRuntimeStore
from .telemetry import RuntimeTelemetry


def _safe_locator(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeValidationError("F14_DIFF_LOCATOR_INVALID")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeValidationError("F14_DIFF_LOCATOR_ESCAPE")
    return path.as_posix()


def _changed_values(
    baseline: Mapping[str, str], current: Mapping[str, str]
) -> tuple[list[str], dict[str, dict[str, str | None]]]:
    keys = sorted(set(baseline) | set(current))
    changed: list[str] = []
    hashes: dict[str, dict[str, str | None]] = {}
    for key in keys:
        locator = _safe_locator(key)
        before = baseline.get(key)
        after = current.get(key)
        if before != after:
            changed.append(locator)
            hashes[locator] = {"before": before, "after": after}
    return changed, hashes


@dataclass(frozen=True)
class DiffIndex:
    baseline_revision: int
    current_revision: int
    changed_files: tuple[str, ...]
    changed_artifacts: tuple[str, ...]
    changed_hashes: Mapping[str, Mapping[str, str | None]]
    explicitly_affected_nodes: tuple[str, ...]
    unknown_impact_nodes: tuple[str, ...]
    stale: bool = False

    def __post_init__(self) -> None:
        if self.baseline_revision < 0 or self.current_revision < 0:
            raise RuntimeValidationError("F14_DIFF_REVISION_INVALID")
        if tuple(sorted(self.changed_files)) != self.changed_files:
            raise RuntimeValidationError("F14_DIFF_FILES_NOT_SORTED")
        if any(_safe_locator(item) != item for item in self.changed_files):
            raise RuntimeValidationError("F14_DIFF_FILE_INVALID")
        if len(set(self.explicitly_affected_nodes)) != len(self.explicitly_affected_nodes):
            raise RuntimeValidationError("F14_DIFF_EXPLICIT_NODE_DUPLICATE")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["changed_files"] = list(self.changed_files)
        value["changed_artifacts"] = list(self.changed_artifacts)
        value["explicitly_affected_nodes"] = list(self.explicitly_affected_nodes)
        value["unknown_impact_nodes"] = list(self.unknown_impact_nodes)
        value["changed_hashes"] = {
            str(key): dict(value)
            for key, value in self.changed_hashes.items()
        }
        return value

    def persist(
        self,
        store: SessionStore,
        *,
        session_id: str,
        project_id: str,
        diff_id: str | None = None,
    ) -> str:
        return DerivedRuntimeStore(store).write_diff_index(
            session_id=session_id,
            project_id=project_id,
            baseline_revision=self.baseline_revision,
            current_revision=self.current_revision,
            stale=self.stale,
            result=self.to_dict(),
            diff_id=diff_id,
        )


class DiffIndexBuilder:
    """只比较受控快照和显式 Graph 影响，不进行名称推断。"""

    @staticmethod
    def build(
        *,
        baseline_revision: int,
        current_revision: int,
        baseline_files: Mapping[str, str] | None,
        current_files: Mapping[str, str] | None,
        baseline_artifacts: Mapping[str, str] | None = None,
        current_artifacts: Mapping[str, str] | None = None,
        explicitly_affected_nodes: Sequence[str] = (),
        unknown_impact_nodes: Sequence[str] = (),
        expected_current_revision: int | None = None,
        stale: bool = False,
        telemetry: RuntimeTelemetry | None = None,
    ) -> DiffIndex:
        if telemetry is not None:
            telemetry.record_runtime_counter("diff_index_builds")
        if baseline_files is None:
            raise RuntimeValidationError("F14_DIFF_BASELINE_MISSING")
        if current_files is None:
            raise RuntimeValidationError("F14_DIFF_CURRENT_SNAPSHOT_MISSING")
        if (
            expected_current_revision is not None
            and current_revision != expected_current_revision
        ):
            raise RuntimeValidationError("F14_DIFF_CURRENT_REVISION_MISMATCH")
        before_files = {_safe_locator(key): value for key, value in baseline_files.items()}
        after_files = {_safe_locator(key): value for key, value in current_files.items()}
        changed_files, changed_hashes = _changed_values(before_files, after_files)
        before_artifacts = dict(baseline_artifacts or {})
        after_artifacts = dict(current_artifacts or {})
        changed_artifacts = sorted(
            key
            for key in set(before_artifacts) | set(after_artifacts)
            if before_artifacts.get(key) != after_artifacts.get(key)
        )
        explicit = tuple(sorted(set(explicitly_affected_nodes)))
        unknown = tuple(sorted(set(unknown_impact_nodes)))
        return DiffIndex(
            baseline_revision=baseline_revision,
            current_revision=current_revision,
            changed_files=tuple(changed_files),
            changed_artifacts=tuple(changed_artifacts),
            changed_hashes=changed_hashes,
            explicitly_affected_nodes=explicit,
            unknown_impact_nodes=unknown,
            stale=stale,
        )


__all__ = ["DiffIndex", "DiffIndexBuilder"]
