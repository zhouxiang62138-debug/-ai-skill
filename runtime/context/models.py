"""F13.1/F13.2 Context Manifest、Budget 与 Package 数据模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from runtime.errors import RuntimeValidationError


_SHA256 = re.compile(r"^[a-f0-9]{64}$")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RuntimeValidationError(f"{name.upper()}_INVALID")
    return value


def _hash(value: object, name: str) -> str:
    text = _text(value, name)
    if not _SHA256.fullmatch(text):
        raise RuntimeValidationError(f"{name.upper()}_INVALID")
    return text


@dataclass(frozen=True)
class ContextBuildRequest:
    """Context Builder 的最小请求，不携带 Prompt、Secret 或模型对象。"""

    session_id: str
    run_id: str
    role: str
    additional_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.session_id, "session_id")
        _text(self.run_id, "run_id")
        _text(self.role, "role")
        try:
            references = tuple(self.additional_references)
        except TypeError as exc:
            raise RuntimeValidationError("CONTEXT_REFERENCES_INVALID") from exc
        if not all(isinstance(reference, str) and reference for reference in references):
            raise RuntimeValidationError("CONTEXT_REFERENCES_INVALID")
        object.__setattr__(self, "additional_references", references)


@dataclass(frozen=True)
class ContextSource:
    """一个可审计、可复现的 Context 来源。"""

    source_type: str
    reference: str
    content_hash: str
    reason: str
    priority: str = "NORMAL"
    delivery_mode: str = "INLINE"
    size: int = 0
    original_size: int = 0
    included_size: int = 0
    is_excerpt: bool = False
    excerpt_strategy: str | None = None
    inline_content: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _text(self.source_type, "source_type")
        _text(self.reference, "reference")
        _hash(self.content_hash, "content_hash")
        _text(self.reason, "reason")
        if self.priority not in {"REQUIRED", "HIGH", "NORMAL", "REFERENCE_ONLY"}:
            raise RuntimeValidationError("CONTEXT_PRIORITY_INVALID")
        if self.delivery_mode not in {"INLINE", "REFERENCE"}:
            raise RuntimeValidationError("CONTEXT_DELIVERY_MODE_INVALID")
        for name in ("size", "original_size", "included_size"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise RuntimeValidationError("CONTEXT_SOURCE_SIZE_INVALID")
        if self.size != self.original_size:
            raise RuntimeValidationError("CONTEXT_SOURCE_SIZE_INVALID")
        if self.delivery_mode == "INLINE":
            if self.inline_content is None or self.included_size != self.original_size:
                raise RuntimeValidationError("CONTEXT_INLINE_SOURCE_INVALID")
        elif self.inline_content is not None or self.included_size != 0:
            raise RuntimeValidationError("CONTEXT_REFERENCE_SOURCE_INVALID")
        if self.is_excerpt:
            if self.included_size >= self.original_size or not self.excerpt_strategy:
                raise RuntimeValidationError("CONTEXT_EXCERPT_INVALID")
        elif self.excerpt_strategy is not None or self.included_size != self.original_size * (
            1 if self.delivery_mode == "INLINE" else 0
        ):
            raise RuntimeValidationError("CONTEXT_EXCERPT_INVALID")
        if self.inline_content is not None and not isinstance(self.inline_content, str):
            raise RuntimeValidationError("CONTEXT_SOURCE_CONTENT_INVALID")

    def to_dict(self) -> dict[str, Any]:
        """返回 Manifest 字段；只在来源适合 inline 时附带小内容。"""

        result: dict[str, Any] = {
            "source_type": self.source_type,
            "reference": self.reference,
            "content_hash": self.content_hash,
            "reason": self.reason,
            "priority": self.priority,
            "delivery_mode": self.delivery_mode,
            "size": self.size,
            "original_size": self.original_size,
            "included_size": self.included_size,
            "is_excerpt": self.is_excerpt,
        }
        if self.excerpt_strategy is not None:
            result["excerpt_strategy"] = self.excerpt_strategy
        if self.inline_content is not None:
            result["content"] = self.inline_content
        return result


@dataclass(frozen=True)
class ContextOmittedSource:
    """因预算或来源数量限制未进入本次 Context 的来源摘要。"""

    reference: str
    content_hash: str
    reason: str
    priority: str
    omission_reason: str
    size: int

    def __post_init__(self) -> None:
        _text(self.reference, "reference")
        _hash(self.content_hash, "content_hash")
        _text(self.reason, "reason")
        _text(self.omission_reason, "omission_reason")
        if self.priority not in {"REQUIRED", "HIGH", "NORMAL", "REFERENCE_ONLY"}:
            raise RuntimeValidationError("CONTEXT_PRIORITY_INVALID")
        if not isinstance(self.size, int) or self.size < 0:
            raise RuntimeValidationError("CONTEXT_SOURCE_SIZE_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "content_hash": self.content_hash,
            "reason": self.reason,
            "priority": self.priority,
            "omission_reason": self.omission_reason,
            "size": self.size,
        }


@dataclass(frozen=True)
class ContextPackage:
    """交给当前 Role 的确定性 Context Package。"""

    context_id: str
    session_id: str
    run_id: str
    role: str
    project_id: str
    workflow_state: str
    project_revision: int
    created_at: str
    project_state_hash: str
    context_policy_hash: str
    budget_fingerprint: str
    sources: tuple[ContextSource, ...]
    omitted_sources: tuple[ContextOmittedSource, ...]
    budget_limit: int
    budget_used: int
    inline_bytes: int
    source_count: int
    inline_source_count: int
    reference_source_count: int
    omitted_source_count: int
    max_inline_bytes: int
    max_source_inline_bytes: int
    max_sources: int
    context_hash: str
    context_type: str = "ROLE_SCOPED"
    excluded_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "context_id",
            "session_id",
            "run_id",
            "role",
            "project_id",
            "workflow_state",
            "created_at",
        ):
            _text(getattr(self, name), name)
        if not isinstance(self.project_revision, int) or self.project_revision < 0:
            raise RuntimeValidationError("CONTEXT_REVISION_INVALID")
        _hash(self.project_state_hash, "project_state_hash")
        _hash(self.context_policy_hash, "context_policy_hash")
        _hash(self.budget_fingerprint, "budget_fingerprint")
        if not isinstance(self.sources, tuple) or not all(
            isinstance(source, ContextSource) for source in self.sources
        ):
            raise RuntimeValidationError("CONTEXT_SOURCES_INVALID")
        if not isinstance(self.omitted_sources, tuple) or not all(
            isinstance(source, ContextOmittedSource) for source in self.omitted_sources
        ):
            raise RuntimeValidationError("CONTEXT_OMITTED_SOURCES_INVALID")
        for name in (
            "budget_limit",
            "budget_used",
            "inline_bytes",
            "source_count",
            "inline_source_count",
            "reference_source_count",
            "omitted_source_count",
            "max_inline_bytes",
            "max_source_inline_bytes",
            "max_sources",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise RuntimeValidationError("CONTEXT_BUDGET_METADATA_INVALID")
        if self.budget_used != self.inline_bytes:
            raise RuntimeValidationError("CONTEXT_BUDGET_METADATA_INVALID")
        if self.budget_used > self.budget_limit or self.budget_used > self.max_inline_bytes:
            raise RuntimeValidationError("CONTEXT_BUDGET_EXCEEDED")
        if self.source_count != len(self.sources):
            raise RuntimeValidationError("CONTEXT_BUDGET_METADATA_INVALID")
        if self.inline_source_count != sum(
            source.delivery_mode == "INLINE" for source in self.sources
        ):
            raise RuntimeValidationError("CONTEXT_BUDGET_METADATA_INVALID")
        if self.reference_source_count != sum(
            source.delivery_mode == "REFERENCE" for source in self.sources
        ):
            raise RuntimeValidationError("CONTEXT_BUDGET_METADATA_INVALID")
        if self.omitted_source_count != len(self.omitted_sources):
            raise RuntimeValidationError("CONTEXT_BUDGET_METADATA_INVALID")
        _hash(self.context_hash, "context_hash")
        if self.context_type not in {"ROLE_SCOPED", "EVALUATOR_INDEPENDENT"}:
            raise RuntimeValidationError("CONTEXT_TYPE_INVALID")
        if not isinstance(self.excluded_sources, tuple) or any(
            not isinstance(item, str) or not item for item in self.excluded_sources
        ):
            raise RuntimeValidationError("CONTEXT_EXCLUDED_SOURCES_INVALID")

    @property
    def manifest(self) -> dict[str, Any]:
        """返回不含隐式运行时对象的可序列化 Manifest。"""

        return {
            "context_id": self.context_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "role": self.role,
            "project_id": self.project_id,
            "workflow_state": self.workflow_state,
            "project_revision": self.project_revision,
            "created_at": self.created_at,
            "project_state_hash": self.project_state_hash,
            "context_policy_hash": self.context_policy_hash,
            "budget_fingerprint": self.budget_fingerprint,
            "sources": [source.to_dict() for source in self.sources],
            "omitted_sources": [
                source.to_dict() for source in self.omitted_sources
            ],
            "budget_limit": self.budget_limit,
            "budget_used": self.budget_used,
            "inline_bytes": self.inline_bytes,
            "source_count": self.source_count,
            "inline_source_count": self.inline_source_count,
            "reference_source_count": self.reference_source_count,
            "omitted_source_count": self.omitted_source_count,
            "max_inline_bytes": self.max_inline_bytes,
            "max_source_inline_bytes": self.max_source_inline_bytes,
            "max_sources": self.max_sources,
            "context_hash": self.context_hash,
            "context_type": self.context_type,
            "excluded_sources": list(self.excluded_sources),
        }

    def to_dict(self) -> dict[str, Any]:
        """Context Package 的显式序列化接口。"""

        return self.manifest


@dataclass(frozen=True)
class ContextSourceDelta:
    """一个来源在两份完整 Manifest 之间的变化摘要。"""

    reference: str
    previous_content_hash: str | None
    current_content_hash: str | None

    def __post_init__(self) -> None:
        _text(self.reference, "reference")
        for name, value in (
            ("previous_content_hash", self.previous_content_hash),
            ("current_content_hash", self.current_content_hash),
        ):
            if value is not None:
                _hash(value, name)
        if self.previous_content_hash is None and self.current_content_hash is None:
            raise RuntimeValidationError("CONTEXT_DELTA_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "previous_content_hash": self.previous_content_hash,
            "current_content_hash": self.current_content_hash,
        }


@dataclass(frozen=True)
class ContextResumePackage:
    """基于 F10 durable manifest 的完整 Context Resume 结果。"""

    context: ContextPackage
    resume_mode: str
    previous_context_id: str | None
    previous_context_hash: str | None
    base_revision: int | None
    current_revision: int
    unchanged_sources: tuple[ContextSourceDelta, ...]
    added_sources: tuple[ContextSourceDelta, ...]
    modified_sources: tuple[ContextSourceDelta, ...]
    removed_sources: tuple[ContextSourceDelta, ...]

    def __post_init__(self) -> None:
        if self.resume_mode not in {"FULL_BUILD", "INCREMENTAL"}:
            raise RuntimeValidationError("CONTEXT_RESUME_MODE_INVALID")
        if self.previous_context_hash is not None:
            _hash(self.previous_context_hash, "previous_context_hash")
        if self.base_revision is not None and (
            not isinstance(self.base_revision, int) or self.base_revision < 0
        ):
            raise RuntimeValidationError("CONTEXT_RESUME_REVISION_INVALID")
        if not isinstance(self.current_revision, int) or self.current_revision < 0:
            raise RuntimeValidationError("CONTEXT_RESUME_REVISION_INVALID")
        if self.current_revision != self.context.project_revision:
            raise RuntimeValidationError("CONTEXT_RESUME_REVISION_INVALID")
        for name in (
            "unchanged_sources",
            "added_sources",
            "modified_sources",
            "removed_sources",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(
                isinstance(item, ContextSourceDelta) for item in values
            ):
                raise RuntimeValidationError("CONTEXT_DELTA_INVALID")

    @property
    def context_id(self) -> str:
        return self.context.context_id

    @property
    def context_hash(self) -> str:
        return self.context.context_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "resume_mode": self.resume_mode,
            "previous_context_id": self.previous_context_id,
            "previous_context_hash": self.previous_context_hash,
            "base_revision": self.base_revision,
            "current_revision": self.current_revision,
            "unchanged_sources": [
                item.to_dict() for item in self.unchanged_sources
            ],
            "added_sources": [item.to_dict() for item in self.added_sources],
            "modified_sources": [
                item.to_dict() for item in self.modified_sources
            ],
            "removed_sources": [
                item.to_dict() for item in self.removed_sources
            ],
        }
