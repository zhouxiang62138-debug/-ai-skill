"""F14-B Deterministic Artifact Index。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..errors import RuntimeValidationError
from ..session_store import SessionStore
from .authority import RuntimeAuthorityVerifier
from .store import DerivedRuntimeStore
from .source_cache import SourceCache
from .telemetry import RuntimeTelemetry


AUTHORITIES = frozenset(
    {
        "USER_EXPLICIT",
        "APPROVED_REQUIREMENT",
        "APPROVED_PRODUCT_SPEC",
        "APPROVED_PLAN",
        "APPROVED_CHANGE_SCOPE",
        "RUNTIME_EVIDENCE",
        "GENERATED_ARTIFACT",
        "HISTORICAL",
        "UNKNOWN",
    }
)
FRESHNESS_VALUES = frozenset({"CURRENT", "STALE", "UNKNOWN"})


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    kind: str
    locator: str
    content_hash: str
    project_revision: int
    policy_hash: str
    producer_role: str
    authority: str
    approval_status: str
    freshness: str
    source_state_ref: str

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.kind or not self.locator:
            raise RuntimeValidationError("F14_ARTIFACT_RECORD_INVALID")
        if len(self.content_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.content_hash
        ):
            raise RuntimeValidationError("F14_ARTIFACT_CONTENT_HASH_INVALID")
        if self.project_revision < 0 or not self.policy_hash:
            raise RuntimeValidationError("F14_ARTIFACT_RECORD_REVISION_INVALID")
        if self.authority not in AUTHORITIES:
            raise RuntimeValidationError("F14_ARTIFACT_AUTHORITY_INVALID")
        if self.freshness not in FRESHNESS_VALUES:
            raise RuntimeValidationError("F14_ARTIFACT_FRESHNESS_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ArtifactIndexBuilder:
    """只根据调用方提供的显式来源生成 Artifact 记录。"""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        project_revision: int,
        policy_hash: str,
        producer_role: str,
        telemetry: RuntimeTelemetry | None = None,
        source_cache: SourceCache | None = None,
        authority_verifier: RuntimeAuthorityVerifier | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.project_revision = project_revision
        self.policy_hash = policy_hash
        self.producer_role = producer_role
        self.telemetry = telemetry or RuntimeTelemetry()
        self.source_cache = source_cache
        self.authority_verifier = authority_verifier or RuntimeAuthorityVerifier(
            self.workspace_root, project_revision=project_revision
        )

    def _canonical_path(self, locator: str) -> tuple[Path, str]:
        if not isinstance(locator, str) or not locator or "\x00" in locator:
            raise RuntimeValidationError("F14_ARTIFACT_LOCATOR_INVALID")
        unresolved = self.workspace_root / locator
        if unresolved.is_symlink():
            raise RuntimeValidationError("F14_ARTIFACT_LOCATOR_ANOMALY")
        candidate = unresolved.resolve()
        try:
            relative = candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise RuntimeValidationError("F14_ARTIFACT_LOCATOR_ESCAPE") from exc
        if not candidate.is_file():
            raise RuntimeValidationError("F14_ARTIFACT_SOURCE_MISSING")
        return candidate, relative.as_posix()

    def index_file(
        self,
        *,
        artifact_id: str,
        kind: str,
        locator: str,
        authority: str,
        approval_status: str,
        source_state_ref: str = "",
        freshness: str = "CURRENT",
    ) -> ArtifactRecord:
        self.telemetry.record_runtime_counter("artifact_index_reads")
        path, canonical_locator = self._canonical_path(locator)
        if self.source_cache is not None:
            protected = authority != "GENERATED_ARTIFACT"
            cached = self.source_cache.read(
                canonical_locator,
                require_trusted_hash=True,
                protected=protected,
                approved=authority.startswith("APPROVED_"),
                security_sensitive=authority
                in {"USER_EXPLICIT", "APPROVED_CHANGE_SCOPE"},
            )
            content_hash = cached.content_hash
        else:
            raw = path.read_bytes()
            self.telemetry.record_file_read(len(raw))
            self.telemetry.record_hash()
            content_hash = hashlib.sha256(raw).hexdigest()
        self.authority_verifier.verify(
            authority=authority,
            locator=canonical_locator,
            content_hash=content_hash,
            approval_status=approval_status,
            source_state_ref=source_state_ref,
        )
        return ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            locator=canonical_locator,
            content_hash=content_hash,
            project_revision=self.project_revision,
            policy_hash=self.policy_hash,
            producer_role=self.producer_role,
            authority=authority,
            approval_status=approval_status,
            freshness=freshness,
            source_state_ref=source_state_ref,
        )

    def build(self, sources: Iterable[Mapping[str, Any]]) -> list[ArtifactRecord]:
        self.telemetry.record_runtime_counter("artifact_index_rebuilds")
        records: list[ArtifactRecord] = []
        seen: set[str] = set()
        for source in sources:
            if not isinstance(source, Mapping):
                raise RuntimeValidationError("F14_ARTIFACT_SOURCE_DECLARATION_INVALID")
            artifact_id = source.get("artifact_id")
            if not isinstance(artifact_id, str) or artifact_id in seen:
                raise RuntimeValidationError("F14_ARTIFACT_ID_DUPLICATE")
            seen.add(artifact_id)
            records.append(
                self.index_file(
                    artifact_id=artifact_id,
                    kind=str(source.get("kind", "")),
                    locator=str(source.get("locator", "")),
                    authority=str(source.get("authority", "UNKNOWN")),
                    approval_status=str(source.get("approval_status", "UNKNOWN")),
                    source_state_ref=str(source.get("source_state_ref", "")),
                    freshness=str(source.get("freshness", "CURRENT")),
                )
            )
        return sorted(records, key=lambda record: record.artifact_id)

    def persist(
        self,
        store: SessionStore,
        *,
        session_id: str,
        project_id: str,
        records: Iterable[ArtifactRecord],
        snapshot_id: str | None = None,
    ) -> str:
        runtime_store = DerivedRuntimeStore(store)
        return runtime_store.write_artifact_snapshot(
            session_id=session_id,
            project_id=project_id,
            project_revision=self.project_revision,
            policy_hash=self.policy_hash,
            records=[record.to_dict() for record in records],
            snapshot_id=snapshot_id,
        )


__all__ = [
    "AUTHORITIES",
    "FRESHNESS_VALUES",
    "ArtifactIndexBuilder",
    "ArtifactRecord",
]
