"""文本描述适配器：读取受路径策略保护的文本，不执行文本内容。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..errors import ReferenceAnalysisError
from ..models import NormalizedReference


class TextDescriptionAdapter:
    source_type = "text_description"

    def __init__(self, *, config: Mapping[str, Any], version: int = 1) -> None:
        self.config = config
        self.version = version

    def normalize(self, source: Mapping[str, Any], *, root: Any, path_policy: Any) -> NormalizedReference:
        locator = source.get("source") or {}
        if not isinstance(locator, Mapping):
            raise ReferenceAnalysisError("TEXT_SOURCE_INVALID")
        text_ref = locator.get("text_ref")
        if not isinstance(text_ref, str) or not text_ref:
            return NormalizedReference(
                reference_id=str(source["reference_id"]),
                source_type=self.source_type,
                source_artifact_ref=str(source.get("scope_ref", "")),
                source_hash=hashlib.sha256(
                    str(locator.get("identifier", "")).encode("utf-8")
                ).hexdigest(),
                scope_ref=str(source["scope_ref"]),
                context=source["context"],
                metadata={"mode": "identifier_only"},
                available_modalities=("text_metadata",),
                limitations=("TEXT_CONTENT_NOT_PROVIDED",),
                adapter_version=self.version,
            )
        path = path_policy.assert_module_path("reference_analysis", root, text_ref, operation="read")
        if not path.is_file():
            raise ReferenceAnalysisError("TEXT_SOURCE_NOT_FOUND")
        max_bytes = int(self.config.get("limits", {}).get("max_text_analysis_bytes", 262144))
        size = path.stat().st_size
        if size > max_bytes:
            raise ReferenceAnalysisError("TEXT_SOURCE_TOO_LARGE")
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReferenceAnalysisError("TEXT_SOURCE_NOT_UTF8") from exc
        digest = hashlib.sha256(raw).hexdigest()
        expected_hash = locator.get("content_hash")
        if expected_hash and str(expected_hash).casefold() != digest:
            raise ReferenceAnalysisError("TEXT_SOURCE_HASH_MISMATCH")
        expected_size = locator.get("size_bytes")
        if expected_size is not None and int(expected_size) != size:
            raise ReferenceAnalysisError("TEXT_SOURCE_SIZE_MISMATCH")
        return NormalizedReference(
            reference_id=str(source["reference_id"]),
            source_type=self.source_type,
            source_artifact_ref=text_ref,
            source_hash=digest,
            scope_ref=str(source["scope_ref"]),
            context=source["context"],
            content=content,
            metadata={"size_bytes": size, "encoding": "utf-8"},
            available_modalities=("text",),
            capabilities={"text_analysis": "available"},
            adapter_version=self.version,
        )
