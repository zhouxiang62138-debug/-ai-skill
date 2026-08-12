"""图像安全注册适配器，不伪造视觉语义分析。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from ..errors import ReferenceAnalysisError
from ..acquisition import image_dimensions
from ..models import NormalizedReference


class ImageAdapter:
    source_type = "image"

    def __init__(self, *, config: Mapping[str, Any], version: int = 1) -> None:
        self.config = config
        self.version = version

    def normalize(self, source: Mapping[str, Any], *, root: Any, path_policy: Any) -> NormalizedReference:
        locator = source.get("source") or {}
        artifact_ref = locator.get("artifact_ref") if isinstance(locator, Mapping) else None
        if not isinstance(artifact_ref, str) or not artifact_ref:
            raise ReferenceAnalysisError("IMAGE_ARTIFACT_REQUIRED")
        path = path_policy.assert_module_path("reference_analysis", root, artifact_ref, operation="read")
        if not path.is_file():
            raise ReferenceAnalysisError("IMAGE_ARTIFACT_NOT_FOUND")
        max_bytes = int(self.config.get("image_capabilities", {}).get("max_bytes", 10485760))
        max_pixels = int(self.config.get("image_capabilities", {}).get("max_pixels", 100000000))
        size = path.stat().st_size
        if size > max_bytes:
            raise ReferenceAnalysisError("IMAGE_TOO_LARGE")
        suffix = path.suffix.casefold()
        formats = {"." + str(item).casefold().lstrip(".") for item in self.config.get("image_capabilities", {}).get("formats", [])}
        if suffix not in formats:
            raise ReferenceAnalysisError("IMAGE_FORMAT_UNSUPPORTED")
        raw = path.read_bytes()
        valid = (
            (suffix == ".png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
            or (suffix in {".jpg", ".jpeg"} and raw.startswith(b"\xff\xd8\xff"))
            or (suffix == ".webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
        )
        if not valid:
            raise ReferenceAnalysisError("IMAGE_INTEGRITY_INVALID")
        digest = hashlib.sha256(raw).hexdigest()
        expected_hash = locator.get("content_hash")
        if expected_hash and str(expected_hash).casefold() != digest:
            raise ReferenceAnalysisError("IMAGE_SOURCE_HASH_MISMATCH")
        expected_size = locator.get("size_bytes")
        if expected_size is not None and int(expected_size) != size:
            raise ReferenceAnalysisError("IMAGE_SOURCE_SIZE_MISMATCH")
        metadata: dict[str, Any] = {"size_bytes": size, "format": suffix[1:]}
        dims = image_dimensions(raw, suffix)
        if dims:
            if dims[0] * dims[1] > max_pixels:
                raise ReferenceAnalysisError("IMAGE_PIXEL_BUDGET_EXCEEDED")
            metadata["width"], metadata["height"] = dims
            metadata["pixel_count"] = dims[0] * dims[1]
        return NormalizedReference(
            reference_id=str(source["reference_id"]),
            source_type=self.source_type,
            source_artifact_ref=artifact_ref,
            source_hash=digest,
            scope_ref=str(source["scope_ref"]),
            context=source["context"],
            metadata=metadata,
            available_modalities=("image_binary",),
            capabilities={
                "visual_semantic_analysis": "supported_with_limitations",
                "layout_analysis": "supported_with_limitations",
                "semantic_provider": "codex_native_multimodal",
                "semantic_transport": "official_codex_sdk",
            },
            limitations=("CODEX_AUTH_AND_RUNTIME_MAY_BE_UNAVAILABLE",),
            adapter_version=self.version,
        )
