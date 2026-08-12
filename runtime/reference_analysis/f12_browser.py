"""把 Reference Browser Acquisition 接到既有 F12 Capability/Network Policy。"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from typing import Any

from runtime.policy import CapabilityPolicy
from runtime.security.network import NetworkPolicy

from .acquisition import resolve_public_addresses
from .browser_acquisition import (
    BrowserAcquisitionProvider,
    BrowserCaptureAdapter,
    PlaywrightReferenceBrowserAdapter,
)
from .errors import ReferenceAnalysisError


class F12ReferenceBrowserRuntime:
    """只使用显式注入的 DNS resolver 和既有 F12 网络策略。"""

    def __init__(
        self,
        *,
        resolver: Callable[..., Any] | None,
        network_policy: NetworkPolicy | None = None,
        capability_policy: CapabilityPolicy | None = None,
        adapter_factory: Callable[[], BrowserCaptureAdapter] | None = None,
        role: str = "reference_analysis",
        service: str = "reference_public_web",
        operation: str = "read",
    ) -> None:
        if resolver is None:
            raise ReferenceAnalysisError("BROWSER_F12_RESOLVER_REQUIRED")
        if role != "reference_analysis":
            raise ReferenceAnalysisError("BROWSER_F12_ROLE_INVALID")
        if service != "reference_public_web" or operation != "read":
            raise ReferenceAnalysisError("BROWSER_F12_SERVICE_INVALID")
        self.role = role
        self.service = service
        self.operation = operation
        self.resolver = resolver
        self.network_policy = network_policy or NetworkPolicy()
        self.capability_policy = capability_policy or CapabilityPolicy()
        self.capability_policy.authorize(role, "browser.access", resource=service, action="reference.acquire")
        self.adapter_factory = adapter_factory
        if self.adapter_factory is None and importlib.util.find_spec("playwright") is not None:
            self.adapter_factory = PlaywrightReferenceBrowserAdapter

    def resolve(self, host: str) -> tuple[str, ...]:
        """仅使用 Host/F12 注入的 resolver，不在模块内主动发起 DNS。"""

        return resolve_public_addresses(host, resolver=self.resolver)

    def authorize_url(self, url: str) -> None:
        self.network_policy.authorize(self.role, self.service, self.operation, url)

    def provider(self) -> BrowserAcquisitionProvider:
        return BrowserAcquisitionProvider(
            adapter_factory=self.adapter_factory,
            resolver=self.resolver,
            network_authorizer=self.authorize_url,
        )

