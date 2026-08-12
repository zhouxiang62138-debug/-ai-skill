"""F14-E 无损增量 Context 基础设施。

本模块只处理可确定性证明的 Context Unit 复用、依赖失效和 Delta。
它不改变 F13 正式 Context Package，也不把摘要或模型结论当作权威来源。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from runtime.errors import RuntimeValidationError
from runtime.deterministic.telemetry import RuntimeTelemetry


REUSE_STATUSES = frozenset(
    {
        "REUSED_EXACT",
        "REBUILT",
        "INVALIDATED",
        "STALE",
        "UNKNOWN",
        "NOT_CACHEABLE",
    }
)
_SHA256 = set("0123456789abcdef")


def _hash(value: str, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _SHA256 for char in value)
    ):
        raise RuntimeValidationError(f"F14_INCREMENTAL_{name.upper()}_INVALID")
    return value


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeValidationError(f"F14_INCREMENTAL_{name.upper()}_INVALID")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IncrementalContextUnit:
    """一个可单独验证和复用的 Context Unit。"""

    id: str
    authority: str
    source_ref: str
    exact_locator: str
    source_hash: str
    project_revision: int
    policy_hash: str
    role_scope: str
    parser_version: str
    summary_version: str
    dependency_hash: str
    delivery_hash: str
    reuse_status: str = "REBUILT"
    invalidation_reason: str | None = None
    kind: str = "source"
    size: int = 0
    cacheable: bool = True
    dependency_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "id",
            "authority",
            "source_ref",
            "exact_locator",
            "role_scope",
            "parser_version",
            "summary_version",
            "kind",
        ):
            _text(getattr(self, name), name)
        for name in (
            "source_hash",
            "policy_hash",
            "dependency_hash",
            "delivery_hash",
        ):
            _hash(getattr(self, name), name)
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("F14_INCREMENTAL_REVISION_INVALID")
        if self.reuse_status not in REUSE_STATUSES:
            raise RuntimeValidationError("F14_INCREMENTAL_REUSE_STATUS_INVALID")
        if self.invalidation_reason is not None:
            _text(self.invalidation_reason, "invalidation_reason")
        if not isinstance(self.size, int) or self.size < 0:
            raise RuntimeValidationError("F14_INCREMENTAL_SIZE_INVALID")
        if not isinstance(self.cacheable, bool):
            raise RuntimeValidationError("F14_INCREMENTAL_CACHEABLE_INVALID")
        if any(not isinstance(item, str) or not item for item in self.dependency_ids):
            raise RuntimeValidationError("F14_INCREMENTAL_DEPENDENCIES_INVALID")
        if self.reuse_status == "NOT_CACHEABLE" and self.cacheable:
            raise RuntimeValidationError("F14_INCREMENTAL_CACHEABLE_STATUS_MISMATCH")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "authority": self.authority,
            "source_ref": self.source_ref,
            "exact_locator": self.exact_locator,
            "source_hash": self.source_hash,
            "project_revision": self.project_revision,
            "policy_hash": self.policy_hash,
            "role_scope": self.role_scope,
            "parser_version": self.parser_version,
            "summary_version": self.summary_version,
            "dependency_hash": self.dependency_hash,
            "delivery_hash": self.delivery_hash,
            "reuse_status": self.reuse_status,
            "invalidation_reason": self.invalidation_reason,
            "kind": self.kind,
            "size": self.size,
            "cacheable": self.cacheable,
            "dependency_ids": list(self.dependency_ids),
        }


@dataclass(frozen=True)
class IncrementalContextManifest:
    """可追加保存、可重建的 Unit 级 Manifest。"""

    role: str
    task_identity: str
    project_revision: int
    policy_hash: str
    parser_version: str
    summary_version: str
    units: tuple[IncrementalContextUnit, ...]
    coverage_result: str = "VALID"
    manifest_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.role, "role")
        _text(self.task_identity, "task_identity")
        _hash(self.policy_hash, "policy_hash")
        _text(self.parser_version, "parser_version")
        _text(self.summary_version, "summary_version")
        if self.coverage_result not in {"VALID", "INVALID", "UNKNOWN"}:
            raise RuntimeValidationError("F14_INCREMENTAL_COVERAGE_INVALID")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("F14_INCREMENTAL_REVISION_INVALID")
        if not isinstance(self.units, tuple) or not all(
            isinstance(unit, IncrementalContextUnit) for unit in self.units
        ):
            raise RuntimeValidationError("F14_INCREMENTAL_UNITS_INVALID")
        if len({unit.id for unit in self.units}) != len(self.units):
            raise RuntimeValidationError("F14_INCREMENTAL_UNIT_ID_DUPLICATE")
        if any(unit.role_scope != self.role for unit in self.units):
            raise RuntimeValidationError("F14_INCREMENTAL_ROLE_SCOPE_INVALID")
        payload = self._payload()
        object.__setattr__(self, "manifest_hash", _stable_hash(payload))

    def _payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "task_identity": self.task_identity,
            "project_revision": self.project_revision,
            "policy_hash": self.policy_hash,
            "parser_version": self.parser_version,
            "summary_version": self.summary_version,
            "coverage_result": self.coverage_result,
            "units": [unit.to_dict() for unit in sorted(self.units, key=lambda item: item.id)],
        }

    def to_dict(self) -> dict[str, Any]:
        value = self._payload()
        value["manifest_hash"] = self.manifest_hash
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "IncrementalContextManifest":
        try:
            units = tuple(
                IncrementalContextUnit(
                    **{
                        **dict(item),
                        "dependency_ids": tuple(item.get("dependency_ids", ())),
                    }
                )
                for item in value["units"]
            )
            manifest = cls(
                role=str(value["role"]),
                task_identity=str(value["task_identity"]),
                project_revision=int(value["project_revision"]),
                policy_hash=str(value["policy_hash"]),
                parser_version=str(value["parser_version"]),
                summary_version=str(value["summary_version"]),
                units=units,
                coverage_result=str(value.get("coverage_result", "VALID")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeValidationError("F14_INCREMENTAL_MANIFEST_INVALID") from exc
        if value.get("manifest_hash") != manifest.manifest_hash:
            raise RuntimeValidationError("F14_INCREMENTAL_MANIFEST_CORRUPT")
        return manifest


@dataclass(frozen=True)
class ContextDelta:
    """两个经过验证的 Manifest 之间的可重建差异。"""

    base_manifest_hash: str | None
    current_revision: int
    reused_units: tuple[str, ...]
    added_units: tuple[str, ...]
    changed_units: tuple[str, ...]
    invalidated_units: tuple[str, ...]
    removed_units: tuple[str, ...]
    unknown_units: tuple[str, ...]
    coverage_result: str
    fallback_required: bool

    def __post_init__(self) -> None:
        if self.base_manifest_hash is not None:
            _hash(self.base_manifest_hash, "base_manifest_hash")
        if not isinstance(self.current_revision, int) or self.current_revision < 0:
            raise RuntimeValidationError("F14_INCREMENTAL_REVISION_INVALID")
        for name in (
            "reused_units",
            "added_units",
            "changed_units",
            "invalidated_units",
            "removed_units",
            "unknown_units",
        ):
            values = getattr(self, name)
            if tuple(sorted(values)) != values or len(set(values)) != len(values):
                raise RuntimeValidationError("F14_INCREMENTAL_DELTA_ORDER_INVALID")
            if any(not isinstance(item, str) or not item for item in values):
                raise RuntimeValidationError("F14_INCREMENTAL_DELTA_UNIT_INVALID")
        if self.coverage_result not in {"VALID", "INVALID", "UNKNOWN"}:
            raise RuntimeValidationError("F14_INCREMENTAL_COVERAGE_INVALID")
        if not isinstance(self.fallback_required, bool):
            raise RuntimeValidationError("F14_INCREMENTAL_FALLBACK_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_manifest_hash": self.base_manifest_hash,
            "current_revision": self.current_revision,
            "reused_units": list(self.reused_units),
            "added_units": list(self.added_units),
            "changed_units": list(self.changed_units),
            "invalidated_units": list(self.invalidated_units),
            "removed_units": list(self.removed_units),
            "unknown_units": list(self.unknown_units),
            "coverage_result": self.coverage_result,
            "fallback_required": self.fallback_required,
        }


class DependencyAwareInvalidator:
    """只沿显式依赖边传播失效；无法证明时返回 UNKNOWN。"""

    def invalidate(
        self,
        units: Iterable[IncrementalContextUnit],
        *,
        changed_node_ids: Iterable[str] = (),
        unknown_node_ids: Iterable[str] = (),
        changed_global_constraints: Iterable[str] = (),
        role: str,
        explicit_edges: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, str]:
        changed = set(changed_node_ids)
        unknown = set(unknown_node_ids)
        global_changes = set(changed_global_constraints)
        adjacency: dict[str, set[str]] = {}
        unknown_edges: list[tuple[str, str]] = []
        for edge in explicit_edges:
            if hasattr(edge, "source") and hasattr(edge, "target"):
                source = str(edge.source)
                target = str(edge.target)
                confidence = str(getattr(edge, "confidence", "unknown"))
            elif isinstance(edge, Mapping):
                source = str(edge.get("source", ""))
                target = str(edge.get("target", ""))
                confidence = str(edge.get("confidence", "unknown"))
            else:
                continue
            if not source or not target:
                continue
            if confidence == "explicit":
                adjacency.setdefault(source, set()).add(target)
            else:
                unknown_edges.append((source, target))
        affected = set(changed)
        frontier = list(changed)
        while frontier:
            current = frontier.pop()
            for target in sorted(adjacency.get(current, ())):
                if target not in affected:
                    affected.add(target)
                    frontier.append(target)
        unknown_impact = set(unknown)
        for source, target in unknown_edges:
            if source in affected or target in affected:
                unknown_impact.update({source, target})
        decisions: dict[str, str] = {}
        for unit in units:
            dependencies = set(unit.dependency_ids)
            if unit.role_scope != role:
                decisions[unit.id] = "UNKNOWN"
            elif unit.authority in {"SECURITY_CONSTRAINT", "PRIVACY_CONSTRAINT", "REGULATORY_CONSTRAINT"} and global_changes:
                decisions[unit.id] = "INVALIDATED"
            elif dependencies & unknown_impact or unit.id in unknown_impact:
                decisions[unit.id] = "UNKNOWN"
            elif dependencies & affected or unit.id in affected:
                decisions[unit.id] = "INVALIDATED"
            else:
                decisions[unit.id] = "REUSED_EXACT"
        return decisions


class IncrementalContextBuilder:
    """从 Full Safe Unit 构建等价的增量结果。"""

    def __init__(
        self,
        *,
        invalidator: DependencyAwareInvalidator | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        self.invalidator = invalidator or DependencyAwareInvalidator()
        self.telemetry = telemetry or RuntimeTelemetry()

    @staticmethod
    def unit_from_source(
        *,
        unit_id: str,
        source_ref: str,
        exact_locator: str,
        source_hash: str,
        authority: str,
        role: str,
        project_revision: int,
        policy_hash: str,
        parser_version: str = "f14-source-v1",
        summary_version: str = "none",
        dependency_ids: Iterable[str] = (),
        delivery: Any = "REFERENCE",
        size: int = 0,
        cacheable: bool = True,
        kind: str = "source",
    ) -> IncrementalContextUnit:
        delivery_hash = _stable_hash(delivery)
        normalized_dependencies = tuple(sorted(set(dependency_ids)))
        dependency_hash = _stable_hash(normalized_dependencies)
        return IncrementalContextUnit(
            id=unit_id,
            authority=authority,
            source_ref=source_ref,
            exact_locator=exact_locator,
            source_hash=source_hash,
            project_revision=project_revision,
            policy_hash=policy_hash,
            role_scope=role,
            parser_version=parser_version,
            summary_version=summary_version,
            dependency_hash=dependency_hash,
            delivery_hash=delivery_hash,
            reuse_status="REBUILT",
            kind=kind,
            size=size,
            cacheable=cacheable,
            dependency_ids=normalized_dependencies,
        )

    def build(
        self,
        *,
        previous: IncrementalContextManifest | None,
        current_units: Iterable[IncrementalContextUnit],
        role: str,
        task_identity: str,
        project_revision: int,
        policy_hash: str,
        parser_version: str,
        summary_version: str,
        changed_node_ids: Iterable[str] = (),
        unknown_node_ids: Iterable[str] = (),
        changed_global_constraints: Iterable[str] = (),
        explicit_edges: Iterable[Mapping[str, Any]] = (),
        coverage_result: str = "VALID",
    ) -> tuple[IncrementalContextManifest, ContextDelta]:
        current = tuple(sorted(current_units, key=lambda unit: unit.id))
        if any(unit.role_scope != role for unit in current):
            raise RuntimeValidationError("F14_INCREMENTAL_ROLE_SCOPE_INVALID")
        decisions = self.invalidator.invalidate(
            current,
            changed_node_ids=changed_node_ids,
            unknown_node_ids=unknown_node_ids,
            changed_global_constraints=changed_global_constraints,
            role=role,
            explicit_edges=explicit_edges,
        )
        previous_by_id = {unit.id: unit for unit in previous.units} if previous else {}
        final: list[IncrementalContextUnit] = []
        reused: list[str] = []
        added: list[str] = []
        changed: list[str] = []
        invalidated: list[str] = []
        unknown: list[str] = []
        for unit in current:
            old = previous_by_id.get(unit.id)
            reason = None
            status = decisions[unit.id]
            if old is None:
                status = "REBUILT"
                added.append(unit.id)
            elif status == "REUSED_EXACT" and self._exact_match(old, unit):
                reused.append(unit.id)
            elif status == "REUSED_EXACT":
                changed.append(unit.id)
                status = "REBUILT"
                reason = self._mismatch_reason(old, unit)
            elif status == "UNKNOWN":
                unknown.append(unit.id)
                status = "UNKNOWN"
                reason = "DEPENDENCY_IMPACT_UNPROVEN"
            else:
                invalidated.append(unit.id)
                status = "INVALIDATED"
                reason = self._mismatch_reason(old, unit)
            final.append(
                IncrementalContextUnit(
                    **{**unit.to_dict(), "reuse_status": status, "invalidation_reason": reason, "dependency_ids": unit.dependency_ids}
                )
            )
        previous_ids = set(previous_by_id)
        current_ids = {unit.id for unit in current}
        removed = sorted(previous_ids - current_ids)
        manifest = IncrementalContextManifest(
            role=role,
            task_identity=task_identity,
            project_revision=project_revision,
            policy_hash=policy_hash,
            parser_version=parser_version,
            summary_version=summary_version,
            units=tuple(final),
            coverage_result=coverage_result,
        )
        fallback = bool(unknown or coverage_result != "VALID")
        delta = ContextDelta(
            base_manifest_hash=previous.manifest_hash if previous else None,
            current_revision=project_revision,
            reused_units=tuple(sorted(reused)),
            added_units=tuple(sorted(added)),
            changed_units=tuple(sorted(changed)),
            invalidated_units=tuple(sorted(set(invalidated))),
            removed_units=tuple(removed),
            unknown_units=tuple(sorted(unknown)),
            coverage_result=coverage_result,
            fallback_required=fallback,
        )
        self.telemetry.record_incremental_build(
            full_rebuild=previous is None,
            units_total=len(final),
            units_reused=len(reused),
            units_rebuilt=len(added) + len(changed),
            units_invalidated=len(invalidated),
            bytes_reused=sum(unit.size for unit in final if unit.id in reused),
            bytes_rebuilt=sum(
                unit.size
                for unit in final
                if unit.id in set(added) | set(changed) | set(invalidated)
            ),
            fallback_full_rebuild=fallback,
        )
        return manifest, delta

    def build_from_diff_index(
        self,
        *,
        diff_index: Any,
        **kwargs: Any,
    ) -> tuple[IncrementalContextManifest, ContextDelta]:
        """把 F14-B Diff Index 转换为增量失效输入，不重新猜测依赖。"""

        changed = tuple(
            sorted(
                set(getattr(diff_index, "changed_files", ()))
                | set(getattr(diff_index, "changed_artifacts", ()))
                | set(getattr(diff_index, "explicitly_affected_nodes", ()))
            )
        )
        unknown = tuple(sorted(set(getattr(diff_index, "unknown_impact_nodes", ()))))
        return self.build(
            changed_node_ids=changed,
            unknown_node_ids=unknown,
            **kwargs,
        )

    @staticmethod
    def _exact_match(previous: IncrementalContextUnit, current: IncrementalContextUnit) -> bool:
        return (
            previous.authority == current.authority
            and previous.source_ref == current.source_ref
            and previous.exact_locator == current.exact_locator
            and previous.source_hash == current.source_hash
            and previous.project_revision == current.project_revision
            and previous.policy_hash == current.policy_hash
            and previous.role_scope == current.role_scope
            and previous.parser_version == current.parser_version
            and previous.summary_version == current.summary_version
            and previous.dependency_hash == current.dependency_hash
            and previous.delivery_hash == current.delivery_hash
            and previous.cacheable
            and current.cacheable
        )

    @staticmethod
    def _mismatch_reason(previous: IncrementalContextUnit, current: IncrementalContextUnit) -> str:
        for label, left, right in (
            ("PROJECT_REVISION_CHANGED", previous.project_revision, current.project_revision),
            ("SOURCE_HASH_CHANGED", previous.source_hash, current.source_hash),
            ("POLICY_HASH_CHANGED", previous.policy_hash, current.policy_hash),
            ("ROLE_SCOPE_CHANGED", previous.role_scope, current.role_scope),
            ("PARSER_VERSION_CHANGED", previous.parser_version, current.parser_version),
            ("SUMMARY_VERSION_CHANGED", previous.summary_version, current.summary_version),
            ("DEPENDENCY_HASH_CHANGED", previous.dependency_hash, current.dependency_hash),
            ("DELIVERY_HASH_CHANGED", previous.delivery_hash, current.delivery_hash),
        ):
            if left != right:
                return label
        return "SOURCE_REVALIDATION_REQUIRED"


def compare_mandatory_context(
    full: IncrementalContextManifest,
    incremental: IncrementalContextManifest,
) -> dict[str, Any]:
    """比较 Full Safe 与 Incremental 的权威 Mandatory 集合。"""

    def mandatory(manifest: IncrementalContextManifest) -> dict[str, dict[str, Any]]:
        return {
            unit.id: {
                "authority": unit.authority,
                "source_ref": unit.source_ref,
                "exact_locator": unit.exact_locator,
                "source_hash": unit.source_hash,
                "project_revision": unit.project_revision,
                "policy_hash": unit.policy_hash,
                "role_scope": unit.role_scope,
            }
            for unit in manifest.units
            if unit.authority != "GENERATED_ARTIFACT"
            and unit.kind in {"requirement", "acceptance_criterion", "constraint", "plan_task", "evidence", "source"}
        }

    left = mandatory(full)
    right = mandatory(incremental)
    metadata_differences: list[str] = []
    if full.role != incremental.role:
        metadata_differences.append("ROLE_SCOPE")
    if full.project_revision != incremental.project_revision:
        metadata_differences.append("REVISION")
    if full.policy_hash != incremental.policy_hash:
        metadata_differences.append("POLICY")
    if full.coverage_result != incremental.coverage_result:
        metadata_differences.append("COVERAGE")
    return {
        "mandatory_difference": sorted(set(left) ^ set(right)),
        "authority_difference": sorted(
            unit_id for unit_id in set(left) & set(right) if left[unit_id]["authority"] != right[unit_id]["authority"]
        ),
        "coverage_difference": metadata_differences,
        "metadata_difference": metadata_differences,
        "full": left,
        "incremental": right,
        "equivalent": left == right and not metadata_differences,
    }


__all__ = [
    "ContextDelta",
    "DependencyAwareInvalidator",
    "IncrementalContextBuilder",
    "IncrementalContextManifest",
    "IncrementalContextUnit",
    "REUSE_STATUSES",
    "compare_mandatory_context",
]
