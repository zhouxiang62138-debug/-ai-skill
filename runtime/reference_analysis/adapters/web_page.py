"""网页引用适配器：只做 URL 合同校验，不直接发起网络请求。"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ..acquisition import normalize_public_url
from ..errors import ReferenceAnalysisError
from ..models import NormalizedReference


class WebPageAdapter:
    source_type = "web_page"

    def __init__(self, *, config: Mapping[str, Any], version: int = 1) -> None:
        self.config = config
        self.version = version

    def normalize(self, source: Mapping[str, Any], *, root: Any, path_policy: Any) -> NormalizedReference:
        locator = source.get("source") or {}
        uri = locator.get("uri") if isinstance(locator, Mapping) else None
        if not isinstance(uri, str) or not uri:
            raise ReferenceAnalysisError("WEB_URL_REQUIRED")
        allowed = set(self.config.get("web_acquisition", {}).get("allowed_schemes", ["https"]))
        canonical, resolved_addresses = normalize_public_url(uri, allowed_schemes=tuple(allowed))
        host = canonical.split("/", 3)[2].split(":", 1)[0]
        rejected = set(self.config.get("web_acquisition", {}).get("reject_hosts", []))
        if host in rejected:
            raise ReferenceAnalysisError("WEB_HOST_REJECTED")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return NormalizedReference(
            reference_id=str(source["reference_id"]),
            source_type=self.source_type,
            source_artifact_ref=str(source.get("scope_ref", "")),
            source_hash=digest,
            scope_ref=str(source["scope_ref"]),
            context=source["context"],
            metadata={
                "canonical_url": canonical,
                "host": host,
                "resolved_addresses": list(resolved_addresses),
            },
            available_modalities=("url_metadata",),
            capabilities={
                "acquisition": "NETWORK_ACQUISITION_DEFERRED",
                "fetch_enabled": False,
            },
            limitations=("NETWORK_ACQUISITION_DEFERRED", "BROWSER_CAPTURE_DEFERRED"),
            adapter_version=self.version,
        )
