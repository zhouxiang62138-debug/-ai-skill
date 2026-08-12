"""F14-B Authority/Provenance 与 Execution/Verification 双语义图。"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

from ..errors import RuntimeValidationError
from ..session_store import SessionStore
from .store import DerivedRuntimeStore
from .telemetry import RuntimeTelemetry


GRAPH_KINDS = frozenset({"authority", "execution"})
AUTHORITY_EDGE_TYPES = frozenset(
    {"derived_from", "approved_by", "supersedes", "sourced_from", "governed_by"}
)
EXECUTION_EDGE_TYPES = frozenset(
    {"satisfies", "implements", "affects", "verifies", "evidenced_by", "regresses_with"}
)
CONFIDENCE_VALUES = frozenset({"explicit", "unknown"})


@dataclass(frozen=True)
class DependencyEdge:
    graph_kind: str
    edge_type: str
    source: str
    target: str
    evidence_ref: str
    source_hash: str
    revision: int
    confidence: str
    edge_id: str = ""

    def __post_init__(self) -> None:
        if self.graph_kind not in GRAPH_KINDS:
            raise RuntimeValidationError("F14_GRAPH_KIND_INVALID")
        allowed = (
            AUTHORITY_EDGE_TYPES
            if self.graph_kind == "authority"
            else EXECUTION_EDGE_TYPES
        )
        if self.edge_type not in allowed:
            raise RuntimeValidationError("F14_GRAPH_EDGE_TYPE_INVALID")
        if not self.source or not self.target or self.source == self.target:
            raise RuntimeValidationError("F14_GRAPH_ENDPOINT_INVALID")
        if self.confidence not in CONFIDENCE_VALUES:
            raise RuntimeValidationError("F14_GRAPH_CONFIDENCE_INVALID")
        if self.revision < 0:
            raise RuntimeValidationError("F14_GRAPH_REVISION_INVALID")
        if self.confidence == "explicit":
            if not self.evidence_ref:
                raise RuntimeValidationError("F14_GRAPH_EVIDENCE_REQUIRED")
            if len(self.source_hash) != 64 or any(
                char not in "0123456789abcdef" for char in self.source_hash
            ):
                raise RuntimeValidationError("F14_GRAPH_SOURCE_HASH_REQUIRED")
        if self.confidence == "unknown" and self.evidence_ref == "":
            object.__setattr__(self, "evidence_ref", "unknown")

    def with_id(self) -> "DependencyEdge":
        if self.edge_id:
            return self
        edge_id = "edge-" + hashlib.sha256(
            "|".join(
                (
                    self.graph_kind,
                    self.edge_type,
                    self.source,
                    self.target,
                    self.evidence_ref,
                    self.source_hash,
                    str(self.revision),
                    self.confidence,
                )
            ).encode("utf-8")
        ).hexdigest()[:24]
        return DependencyEdge(**{**asdict(self), "edge_id": edge_id})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DependencyGraph:
    """保存显式边；不会根据名称或自然语言自动推断边。"""

    def __init__(self, edges: Iterable[DependencyEdge] = (), *, telemetry: RuntimeTelemetry | None = None) -> None:
        if telemetry is not None:
            telemetry.record_runtime_counter("dependency_graph_rebuilds")
        resolved = [edge.with_id() for edge in edges]
        if len({edge.edge_id for edge in resolved}) != len(resolved):
            raise RuntimeValidationError("F14_GRAPH_EDGE_DUPLICATE")
        self.edges = tuple(sorted(resolved, key=lambda edge: edge.edge_id))

    @classmethod
    def from_mappings(cls, values: Iterable[Mapping[str, Any]], *, telemetry: RuntimeTelemetry | None = None) -> "DependencyGraph":
        edges: list[DependencyEdge] = []
        for value in values:
            if not isinstance(value, Mapping):
                raise RuntimeValidationError("F14_GRAPH_EDGE_INVALID")
            edges.append(DependencyEdge(**dict(value)))
        return cls(edges, telemetry=telemetry)

    @property
    def explicit_edges(self) -> tuple[DependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.confidence == "explicit")

    @property
    def unknown_edges(self) -> tuple[DependencyEdge, ...]:
        return tuple(edge for edge in self.edges if edge.confidence == "unknown")

    def to_dicts(self) -> list[dict[str, Any]]:
        return [edge.to_dict() for edge in self.edges]

    def persist(
        self,
        store: SessionStore,
        *,
        session_id: str,
        project_id: str,
        project_revision: int,
        policy_hash: str,
        snapshot_id: str | None = None,
    ) -> str:
        return DerivedRuntimeStore(store).write_dependency_snapshot(
            session_id=session_id,
            project_id=project_id,
            project_revision=project_revision,
            policy_hash=policy_hash,
            edges=self.to_dicts(),
            snapshot_id=snapshot_id,
        )


__all__ = [
    "AUTHORITY_EDGE_TYPES",
    "CONFIDENCE_VALUES",
    "DependencyEdge",
    "DependencyGraph",
    "EXECUTION_EDGE_TYPES",
    "GRAPH_KINDS",
]
