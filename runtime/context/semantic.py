"""F14-C1 Context Semantic Model。

该模型只描述 Context 的语义、来源和依赖关系，不改变当前 F13 正式交付内容。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from runtime.errors import RuntimeValidationError

from .models import ContextBuildRequest, ContextPackage


CONTEXT_CLASSES = frozenset(
    {"mandatory", "task_relevant", "on_demand", "omitted", "unknown"}
)
DEPENDENCY_ROOT_NAMES = frozenset(
    {
        "requirements",
        "acceptance_criteria",
        "issues",
        "plan_tasks",
        "changed_files",
        "change_request_items",
    }
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ContextUnit:
    """Context 中可独立审计的最小来源单元。"""

    id: str
    context_class: str
    authority: str
    source_ref: str
    source_hash: str
    exact_locator: str
    project_revision: int
    role_scope: str
    reason: str
    dependency_path: tuple[str, ...] = ()
    delivery_mode: str = "REFERENCE"
    size: int = 0

    def __post_init__(self) -> None:
        for name in (
            "id",
            "authority",
            "source_ref",
            "source_hash",
            "exact_locator",
            "role_scope",
            "reason",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise RuntimeValidationError("CONTEXT_UNIT_INVALID")
        if self.context_class not in CONTEXT_CLASSES:
            raise RuntimeValidationError("CONTEXT_UNIT_CLASS_INVALID")
        if len(self.source_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.source_hash
        ):
            raise RuntimeValidationError("CONTEXT_UNIT_HASH_INVALID")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("CONTEXT_UNIT_REVISION_INVALID")
        if self.delivery_mode not in {"INLINE", "REFERENCE", "SHADOW"}:
            raise RuntimeValidationError("CONTEXT_UNIT_DELIVERY_INVALID")
        if not isinstance(self.size, int) or self.size < 0:
            raise RuntimeValidationError("CONTEXT_UNIT_SIZE_INVALID")
        if any(not isinstance(item, str) or not item for item in self.dependency_path):
            raise RuntimeValidationError("CONTEXT_UNIT_DEPENDENCY_PATH_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.context_class,
            "authority": self.authority,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "exact_locator": self.exact_locator,
            "project_revision": self.project_revision,
            "role_scope": self.role_scope,
            "reason": self.reason,
            "dependency_path": list(self.dependency_path),
            "delivery_mode": self.delivery_mode,
            "size": self.size,
        }


@dataclass(frozen=True)
class ContextSemanticModel:
    """按 Role、Task、Revision 绑定的 Context 语义快照。"""

    task_identity: str
    role: str
    project_revision: int
    dependency_roots: Mapping[str, tuple[str, ...]]
    constraints: Mapping[str, tuple[str, ...]]
    context_classes: Mapping[str, tuple[ContextUnit, ...]]
    model_hash: str

    def __post_init__(self) -> None:
        if not self.task_identity or not self.role:
            raise RuntimeValidationError("CONTEXT_SEMANTIC_IDENTITY_INVALID")
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("CONTEXT_SEMANTIC_REVISION_INVALID")
        if set(self.dependency_roots) - DEPENDENCY_ROOT_NAMES:
            raise RuntimeValidationError("CONTEXT_SEMANTIC_ROOT_INVALID")
        for values in self.dependency_roots.values():
            if any(not isinstance(item, str) or not item for item in values):
                raise RuntimeValidationError("CONTEXT_SEMANTIC_ROOT_INVALID")
        for name in ("global_constraints", "task_constraints", "approval_constraints", "safety_constraints"):
            values = self.constraints.get(name, ())
            if any(not isinstance(item, str) or not item for item in values):
                raise RuntimeValidationError("CONTEXT_SEMANTIC_CONSTRAINT_INVALID")
        if set(self.context_classes) != CONTEXT_CLASSES:
            raise RuntimeValidationError("CONTEXT_SEMANTIC_CLASSES_INVALID")
        for values in self.context_classes.values():
            if not all(isinstance(item, ContextUnit) for item in values):
                raise RuntimeValidationError("CONTEXT_SEMANTIC_UNITS_INVALID")
            if any(item.role_scope != self.role for item in values):
                raise RuntimeValidationError("CONTEXT_SEMANTIC_ROLE_SCOPE_INVALID")
        if len(self.model_hash) != 64 or any(
            char not in "0123456789abcdef" for char in self.model_hash
        ):
            raise RuntimeValidationError("CONTEXT_SEMANTIC_HASH_INVALID")

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "task_identity": self.task_identity,
            "role": self.role,
            "project_revision": self.project_revision,
            "dependency_roots": {
                key: list(value) for key, value in sorted(self.dependency_roots.items())
            },
            "constraints": {
                key: list(value) for key, value in sorted(self.constraints.items())
            },
            "context_classes": {
                key: [unit.to_dict() for unit in self.context_classes[key]]
                for key in sorted(self.context_classes)
            },
            "model_hash": self.model_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.manifest


class ContextSemanticBuilder:
    """从当前 F13 Package 构建 C1 语义快照，不改写 Package。"""

    def build(
        self,
        request: ContextBuildRequest,
        current: ContextPackage,
        *,
        dependency_roots: Mapping[str, tuple[str, ...]] | None = None,
        constraints: Mapping[str, tuple[str, ...]] | None = None,
    ) -> ContextSemanticModel:
        if current.role != request.role or current.run_id != request.run_id:
            raise RuntimeValidationError("CONTEXT_SEMANTIC_CONTEXT_BINDING_MISMATCH")
        roots = dict(dependency_roots or request.dependency_roots)
        normalized_roots = {
            key: tuple(value) for key, value in roots.items() if key in DEPENDENCY_ROOT_NAMES
        }
        normalized_constraints = {
            "global_constraints": tuple(
                (constraints or {}).get("global_constraints", request.global_constraints)
            ),
            "task_constraints": tuple(
                (constraints or {}).get("task_constraints", request.task_constraints)
            ),
            "approval_constraints": tuple(
                (constraints or {}).get("approval_constraints", request.approval_constraints)
            ),
            "safety_constraints": tuple(
                (constraints or {}).get("safety_constraints", request.safety_constraints)
            ),
        }
        classes: dict[str, list[ContextUnit]] = {
            name: [] for name in CONTEXT_CLASSES
        }
        for source in current.sources:
            context_class = {
                "REQUIRED": "mandatory",
                "HIGH": "task_relevant",
                "NORMAL": "task_relevant",
                "REFERENCE_ONLY": "on_demand",
            }.get(source.priority, "unknown")
            unit = ContextUnit(
                id=f"source:{source.reference}",
                context_class=context_class,
                authority="UNKNOWN",
                source_ref=source.reference,
                source_hash=source.content_hash,
                exact_locator=source.reference,
                project_revision=current.project_revision,
                role_scope=current.role,
                reason=source.reason,
                dependency_path=(),
                delivery_mode=source.delivery_mode,
                size=source.size,
            )
            classes[context_class].append(unit)
        for source in current.omitted_sources:
            classes["omitted"].append(
                ContextUnit(
                    id=f"source:{source.reference}",
                    context_class="omitted",
                    authority="UNKNOWN",
                    source_ref=source.reference,
                    source_hash=source.content_hash,
                    exact_locator=source.reference,
                    project_revision=current.project_revision,
                    role_scope=current.role,
                    reason=source.reason,
                    delivery_mode="SHADOW",
                    size=source.size,
                )
            )
        task_identity = request.task_identity or f"{request.role}:{request.run_id}"
        if set(normalized_roots) - DEPENDENCY_ROOT_NAMES:
            raise RuntimeValidationError("CONTEXT_SEMANTIC_ROOT_INVALID")
        payload = {
            "task_identity": task_identity,
            "role": request.role,
            "project_revision": current.project_revision,
            "dependency_roots": normalized_roots,
            "constraints": normalized_constraints,
            "context_classes": {
                key: [unit.to_dict() for unit in sorted(value, key=lambda item: item.id)]
                for key, value in classes.items()
            },
        }
        model_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        return ContextSemanticModel(
            task_identity=task_identity,
            role=request.role,
            project_revision=current.project_revision,
            dependency_roots=normalized_roots,
            constraints=normalized_constraints,
            context_classes={
                key: tuple(sorted(value, key=lambda item: item.id))
                for key, value in classes.items()
            },
            model_hash=model_hash,
        )


__all__ = [
    "CONTEXT_CLASSES",
    "DEPENDENCY_ROOT_NAMES",
    "ContextSemanticBuilder",
    "ContextSemanticModel",
    "ContextUnit",
]
