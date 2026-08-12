"""F14-C2 Shadow Classification 与 C3 Coverage Gate。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from runtime.errors import RuntimeValidationError

from .semantic import CONTEXT_CLASSES, ContextSemanticModel, ContextUnit


COVERAGE_STATES = frozenset(
    {"COVERED", "MISSING", "STALE", "CONFLICT", "UNKNOWN", "NOT_APPLICABLE"}
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _edge_value(edge: Any, name: str, default: Any = None) -> Any:
    if isinstance(edge, Mapping):
        return edge.get(name, default)
    return getattr(edge, name, default)


@dataclass(frozen=True)
class ShadowClassification:
    """Shadow 阶段对来源单元和依赖闭包的确定性分类。"""

    task_identity: str
    role: str
    project_revision: int
    units_by_class: Mapping[str, tuple[ContextUnit, ...]]
    mandatory_ids: tuple[str, ...]
    unknown_ids: tuple[str, ...]
    stale_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    dependency_paths: Mapping[str, tuple[str, ...]]
    missing_mandatory_ids: tuple[str, ...]
    classification_hash: str

    def __post_init__(self) -> None:
        if set(self.units_by_class) != CONTEXT_CLASSES:
            raise RuntimeValidationError("SHADOW_CLASSES_INVALID")
        for values in self.units_by_class.values():
            if not all(isinstance(item, ContextUnit) for item in values):
                raise RuntimeValidationError("SHADOW_UNITS_INVALID")
        for name in (
            "mandatory_ids",
            "unknown_ids",
            "stale_ids",
            "conflict_ids",
            "missing_mandatory_ids",
        ):
            values = getattr(self, name)
            if tuple(sorted(set(values))) != values:
                raise RuntimeValidationError("SHADOW_CLASSIFICATION_ORDER_INVALID")
        if len(self.classification_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.classification_hash
        ):
            raise RuntimeValidationError("SHADOW_CLASSIFICATION_HASH_INVALID")

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "task_identity": self.task_identity,
            "role": self.role,
            "project_revision": self.project_revision,
            "units_by_class": {
                key: [unit.to_dict() for unit in self.units_by_class[key]]
                for key in sorted(self.units_by_class)
            },
            "mandatory_ids": list(self.mandatory_ids),
            "unknown_ids": list(self.unknown_ids),
            "stale_ids": list(self.stale_ids),
            "conflict_ids": list(self.conflict_ids),
            "dependency_paths": {
                key: list(value) for key, value in sorted(self.dependency_paths.items())
            },
            "missing_mandatory_ids": list(self.missing_mandatory_ids),
            "classification_hash": self.classification_hash,
        }


class ShadowClassifier:
    """按全局约束、任务闭包和显式依赖执行 Shadow 分类。"""

    def classify(
        self,
        semantic: ContextSemanticModel,
        units: Iterable[ContextUnit] | None = None,
        *,
        global_mandatory_units: Iterable[ContextUnit] = (),
        dependency_edges: Iterable[Any] = (),
    ) -> ShadowClassification:
        all_units = list(units or [
            unit
            for values in semantic.context_classes.values()
            for unit in values
        ])
        all_units.extend(global_mandatory_units)
        by_id: dict[str, ContextUnit] = {}
        conflict_ids: set[str] = set()
        for unit in all_units:
            previous = by_id.get(unit.id)
            if previous is not None and (
                previous.source_hash != unit.source_hash
                or previous.authority != unit.authority
            ):
                conflict_ids.add(unit.id)
            by_id[unit.id] = unit

        mandatory_ids: set[str] = {
            unit.id
            for unit in all_units
            if unit.context_class == "mandatory"
        }
        for name in (
            "global_constraints",
            "task_constraints",
            "approval_constraints",
            "safety_constraints",
        ):
            mandatory_ids.update(semantic.constraints.get(name, ()))
        mandatory_ids.update(
            root
            for values in semantic.dependency_roots.values()
            for root in values
        )

        adjacency: dict[str, list[tuple[str, str]]] = {}
        unknown_ids: set[str] = set()
        unknown_edges: list[tuple[str, str]] = []
        for edge in dependency_edges:
            source = _edge_value(edge, "source")
            target = _edge_value(edge, "target")
            if not isinstance(source, str) or not isinstance(target, str):
                raise RuntimeValidationError("SHADOW_EDGE_INVALID")
            confidence = str(_edge_value(edge, "confidence", "unknown"))
            if confidence != "explicit":
                unknown_ids.update((source, target))
                unknown_edges.append((source, target))
                continue
            adjacency.setdefault(source, []).append((target, source))

        for source, target in unknown_edges:
            if source in mandatory_ids or target in mandatory_ids:
                # 与 Mandatory 闭包相连的未知边必须显式暴露为待处理影响。
                mandatory_ids.update((source, target))

        dependency_paths: dict[str, tuple[str, ...]] = {
            item: (item,) for item in mandatory_ids
        }
        queue = list(sorted(mandatory_ids))
        while queue:
            current = queue.pop(0)
            for target, _ in sorted(adjacency.get(current, [])):
                path = dependency_paths[current] + (target,)
                if target not in dependency_paths:
                    dependency_paths[target] = path
                    mandatory_ids.add(target)
                    queue.append(target)

        missing_mandatory_ids = {
            item for item in mandatory_ids if item not in by_id
        }
        stale_ids = {
            item.id
            for item in by_id.values()
            if item.project_revision != semantic.project_revision
        }
        unknown_ids.update(
            item.id for item in by_id.values() if item.authority == "UNKNOWN"
        )
        unknown_ids.update(missing_mandatory_ids)

        classes: dict[str, list[ContextUnit]] = {name: [] for name in CONTEXT_CLASSES}
        for unit in sorted(by_id.values(), key=lambda item: item.id):
            if unit.id in mandatory_ids:
                class_name = "mandatory"
            elif unit.context_class in CONTEXT_CLASSES:
                class_name = unit.context_class
            else:
                class_name = "unknown"
            classes[class_name].append(unit)
        normalized = {
            key: tuple(classes[key]) for key in sorted(classes)
        }
        payload = {
            "task_identity": semantic.task_identity,
            "role": semantic.role,
            "project_revision": semantic.project_revision,
            "units_by_class": {
                key: [unit.to_dict() for unit in normalized[key]]
                for key in sorted(normalized)
            },
            "mandatory_ids": sorted(mandatory_ids),
            "unknown_ids": sorted(unknown_ids),
            "stale_ids": sorted(stale_ids),
            "conflict_ids": sorted(conflict_ids),
            "dependency_paths": {
                key: list(value) for key, value in sorted(dependency_paths.items())
            },
            "missing_mandatory_ids": sorted(missing_mandatory_ids),
        }
        return ShadowClassification(
            task_identity=semantic.task_identity,
            role=semantic.role,
            project_revision=semantic.project_revision,
            units_by_class=normalized,
            mandatory_ids=tuple(sorted(mandatory_ids)),
            unknown_ids=tuple(sorted(unknown_ids)),
            stale_ids=tuple(sorted(stale_ids)),
            conflict_ids=tuple(sorted(conflict_ids)),
            dependency_paths={key: tuple(value) for key, value in dependency_paths.items()},
            missing_mandatory_ids=tuple(sorted(missing_mandatory_ids)),
            classification_hash=hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class ShadowComparison:
    """当前 F13 Context 与候选 Context 的 Shadow 对照结果。"""

    current_context_bytes: int
    candidate_context_bytes: int
    potential_reduction_bytes: int
    potential_reduction_ratio: float
    mandatory_total: int
    mandatory_covered: int
    mandatory_missing: tuple[str, ...]
    mandatory_stale: tuple[str, ...]
    mandatory_conflict: tuple[str, ...]
    mandatory_unknown: tuple[str, ...]
    task_relevant_count: int
    on_demand_count: int
    omitted_count: int
    fallback_count: int
    coverage: Mapping[str, str]
    current_source_ids: tuple[str, ...]
    candidate_source_ids: tuple[str, ...]
    comparison_hash: str

    def __post_init__(self) -> None:
        for value in (
            self.current_context_bytes,
            self.candidate_context_bytes,
            self.potential_reduction_bytes,
            self.mandatory_total,
            self.mandatory_covered,
            self.task_relevant_count,
            self.on_demand_count,
            self.omitted_count,
            self.fallback_count,
        ):
            if not isinstance(value, int) or value < 0:
                raise RuntimeValidationError("SHADOW_METRICS_INVALID")
        if not 0 <= self.potential_reduction_ratio <= 1:
            raise RuntimeValidationError("SHADOW_METRICS_INVALID")
        if any(value not in COVERAGE_STATES for value in self.coverage.values()):
            raise RuntimeValidationError("SHADOW_COVERAGE_STATE_INVALID")
        if len(self.comparison_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.comparison_hash
        ):
            raise RuntimeValidationError("SHADOW_COMPARISON_HASH_INVALID")

    @property
    def mandatory_complete(self) -> bool:
        return self.mandatory_total == self.mandatory_covered

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "current_context_bytes": self.current_context_bytes,
            "candidate_context_bytes": self.candidate_context_bytes,
            "potential_reduction_bytes": self.potential_reduction_bytes,
            "potential_reduction_ratio": self.potential_reduction_ratio,
            "mandatory_total": self.mandatory_total,
            "mandatory_covered": self.mandatory_covered,
            "mandatory_missing": list(self.mandatory_missing),
            "mandatory_stale": list(self.mandatory_stale),
            "mandatory_conflict": list(self.mandatory_conflict),
            "mandatory_unknown": list(self.mandatory_unknown),
            "task_relevant_count": self.task_relevant_count,
            "on_demand_count": self.on_demand_count,
            "omitted_count": self.omitted_count,
            "fallback_count": self.fallback_count,
            "coverage": dict(sorted(self.coverage.items())),
            "current_source_ids": list(self.current_source_ids),
            "candidate_source_ids": list(self.candidate_source_ids),
            "comparison_hash": self.comparison_hash,
        }


class ShadowComparisonBuilder:
    """构建 Shadow Comparison，不触碰正式 Model Context。"""

    def compare(
        self,
        classification: ShadowClassification,
        current_units: Iterable[ContextUnit],
        candidate_units: Iterable[ContextUnit],
    ) -> ShadowComparison:
        current = {unit.id: unit for unit in current_units}
        candidate = {unit.id: unit for unit in candidate_units}
        coverage: dict[str, str] = {}
        missing: set[str] = set()
        stale: set[str] = set()
        conflict: set[str] = set(classification.conflict_ids)
        unknown: set[str] = set()
        for unit_id in classification.mandatory_ids:
            current_unit = current.get(unit_id)
            candidate_unit = candidate.get(unit_id)
            if candidate_unit is None:
                coverage[unit_id] = "MISSING"
                missing.add(unit_id)
            elif unit_id in classification.conflict_ids:
                coverage[unit_id] = "CONFLICT"
                conflict.add(unit_id)
            elif candidate_unit.project_revision != classification.project_revision:
                coverage[unit_id] = "STALE"
                stale.add(unit_id)
            elif unit_id in classification.unknown_ids or candidate_unit.authority == "UNKNOWN":
                coverage[unit_id] = "UNKNOWN"
                unknown.add(unit_id)
            elif current_unit is not None and current_unit.source_hash != candidate_unit.source_hash:
                coverage[unit_id] = "CONFLICT"
                conflict.add(unit_id)
            else:
                coverage[unit_id] = "COVERED"

        current_bytes = sum(unit.size for unit in current.values())
        candidate_bytes = sum(unit.size for unit in candidate.values())
        reduction = max(0, current_bytes - candidate_bytes)
        ratio = reduction / current_bytes if current_bytes else 0.0
        covered = sum(value == "COVERED" for value in coverage.values())
        payload = {
            "current_context_bytes": current_bytes,
            "candidate_context_bytes": candidate_bytes,
            "potential_reduction_bytes": reduction,
            "potential_reduction_ratio": ratio,
            "mandatory_total": len(classification.mandatory_ids),
            "mandatory_covered": covered,
            "mandatory_missing": sorted(missing),
            "mandatory_stale": sorted(stale),
            "mandatory_conflict": sorted(conflict),
            "mandatory_unknown": sorted(unknown),
            "task_relevant_count": len(classification.units_by_class["task_relevant"]),
            "on_demand_count": len(classification.units_by_class["on_demand"]),
            "omitted_count": len(classification.units_by_class["omitted"]),
            "fallback_count": 0,
            "coverage": dict(sorted(coverage.items())),
            "current_source_ids": sorted(current),
            "candidate_source_ids": sorted(candidate),
        }
        return ShadowComparison(
            current_context_bytes=current_bytes,
            candidate_context_bytes=candidate_bytes,
            potential_reduction_bytes=reduction,
            potential_reduction_ratio=ratio,
            mandatory_total=len(classification.mandatory_ids),
            mandatory_covered=covered,
            mandatory_missing=tuple(sorted(missing)),
            mandatory_stale=tuple(sorted(stale)),
            mandatory_conflict=tuple(sorted(conflict)),
            mandatory_unknown=tuple(sorted(unknown)),
            task_relevant_count=len(classification.units_by_class["task_relevant"]),
            on_demand_count=len(classification.units_by_class["on_demand"]),
            omitted_count=len(classification.units_by_class["omitted"]),
            fallback_count=0,
            coverage=dict(sorted(coverage.items())),
            current_source_ids=tuple(sorted(current)),
            candidate_source_ids=tuple(sorted(candidate)),
            comparison_hash=hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class ShadowGateResult:
    result: str
    reasons: tuple[str, ...]
    fallback_to_f13: bool
    comparison_hash: str

    @property
    def passed(self) -> bool:
        return self.result == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "reasons": list(self.reasons),
            "fallback_to_f13": self.fallback_to_f13,
            "comparison_hash": self.comparison_hash,
        }


class ShadowCoverageGate:
    """C3 门禁：只有完整覆盖才算通过，未知不等于无影响。"""

    def evaluate(self, comparison: ShadowComparison) -> ShadowGateResult:
        reasons: list[str] = []
        if comparison.mandatory_missing:
            reasons.append("mandatory_missing")
        if comparison.mandatory_stale:
            reasons.append("mandatory_stale")
        if comparison.mandatory_conflict:
            reasons.append("mandatory_conflict")
        if comparison.mandatory_unknown:
            reasons.append("mandatory_unknown")
        return ShadowGateResult(
            result="PASS" if not reasons else "FAIL",
            reasons=tuple(reasons),
            fallback_to_f13=bool(reasons),
            comparison_hash=comparison.comparison_hash,
        )


__all__ = [
    "COVERAGE_STATES",
    "ShadowClassification",
    "ShadowClassifier",
    "ShadowComparison",
    "ShadowComparisonBuilder",
    "ShadowCoverageGate",
    "ShadowGateResult",
]
