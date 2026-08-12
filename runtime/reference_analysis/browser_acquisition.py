"""RA7-D 受控 Browser/Web Acquisition Provider。

本模块只接收显式注入的浏览器适配器、DNS resolver 和 F12 网络授权器。
缺少任一能力时 fail closed，不会自行打开网络、读取 Cookie 或使用宿主凭据。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import yaml

from runtime.browser.errors import BrowserEnvironmentBlocked
from runtime.browser.models import safe_text, safe_url
from runtime.execution.path_policy import ExecutionPathPolicy
from scripts.reference_protocol import assert_valid, validate_browser_capture

from .acquisition import (
    AcquisitionOutput,
    AcquisitionRequest,
    AcquiredArtifact,
    BrowserIsolationContract,
    ProviderAvailability,
    ProviderCapability,
    normalize_public_url,
    validate_redirect_chain,
)
from .errors import ReferenceAnalysisError


_CAPTURE_ID = re.compile(r"^CAP-[a-f0-9]{16}-v[0-9]+$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_json_bytes(value: Any, *, max_bytes: int) -> bytes:
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ReferenceAnalysisError("BROWSER_CAPTURE_CONTENT_INVALID") from exc
    safe = safe_text(text, limit=max_bytes)
    raw = safe.encode("utf-8")
    if len(raw) > max_bytes:
        raise ReferenceAnalysisError("BROWSER_CAPTURE_CONTENT_LIMIT_EXCEEDED")
    return raw


def _safe_string_list(value: Any, *, limit: int, url_values: bool = False) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    values: list[str] = []
    for item in value[:limit]:
        values.append(safe_url(item) if url_values else safe_text(item, limit=1000))
    return tuple(values)


class BrowserCaptureAdapter(Protocol):
    """Reference Acquisition 所需的最小浏览器适配器。

    页面内容永远作为不可信数据返回；适配器不得把页面文本解释成 Runtime 指令。
    """

    def configure_isolation(self, contract: BrowserIsolationContract) -> None: ...

    def set_navigation_guard(self, guard: Callable[[str], str]) -> None: ...

    def start(self) -> None: ...

    def set_viewport(self, width: int, height: int) -> None: ...

    def navigate(self, url: str, *, timeout_ms: int) -> None: ...

    def redirect_chain(self) -> Sequence[str]: ...

    def dom_snapshot(self, *, max_bytes: int) -> Any: ...

    def computed_style_snapshot(self, *, max_bytes: int) -> Any: ...

    def screenshot(self, path: Path) -> None: ...

    def console_errors(self) -> Sequence[str]: ...

    def failed_requests(self) -> Sequence[str]: ...

    def runtime_identity(self) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


class PlaywrightReferenceBrowserAdapter:
    """使用 Playwright 临时上下文的真实 Browser Capture Adapter。

    这个适配器只负责浏览器执行；URL 解析、DNS、F12 网络授权和项目路径仍由
    ``BrowserAcquisitionProvider`` 以及调用方显式注入。依赖或授权缺失时直接阻断。
    """

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._isolation: BrowserIsolationContract | None = None
        self._navigation_guard: Callable[[str], str] | None = None
        self._viewport = BrowserViewport()
        self._document_urls: list[str] = []
        self._console_errors: list[str] = []
        self._failed_requests: list[str] = []

    def configure_isolation(self, contract: BrowserIsolationContract) -> None:
        contract.validate()
        self._isolation = contract

    def set_navigation_guard(self, guard: Callable[[str], str]) -> None:
        if not callable(guard):
            raise ReferenceAnalysisError("BROWSER_NAVIGATION_GUARD_REQUIRED")
        self._navigation_guard = guard

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserEnvironmentBlocked("BROWSER_NOT_STARTED")
        return self._page

    def _record_console(self, message: Any) -> None:
        if getattr(message, "type", None) == "error" and len(self._console_errors) < 64:
            self._console_errors.append(safe_text(getattr(message, "text", ""), limit=1000))

    def _record_failed_request(self, request: Any) -> None:
        if len(self._failed_requests) < 64:
            self._failed_requests.append(safe_url(getattr(request, "url", "")))

    def _guard_request(self, route: Any) -> None:
        request = route.request
        url = str(request.url)
        try:
            if self._navigation_guard is None:
                raise ReferenceAnalysisError("BROWSER_NAVIGATION_GUARD_REQUIRED")
            self._navigation_guard(url)
            if request.resource_type == "document" and url not in self._document_urls:
                self._document_urls.append(url)
            route.continue_()
        except Exception:
            self._record_failed_request(request)
            route.abort()

    def start(self) -> None:
        if self._isolation is None:
            self.configure_isolation(BrowserIsolationContract())
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserEnvironmentBlocked("BROWSER_PLAYWRIGHT_UNAVAILABLE") from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            # new_context 不读取用户 Profile、Cookie、密码或扩展，并拒绝下载。
            self._context = self._browser.new_context(
                accept_downloads=False,
                service_workers="block",
                viewport=None,
            )
            self._context.set_default_timeout(30000)
            self._page = self._context.new_page()
            self._page.on("console", self._record_console)
            self._page.on("requestfailed", self._record_failed_request)
            self._page.on("popup", lambda popup: popup.close())
            self._page.route("**/*", self._guard_request)
        except Exception as exc:
            self.close()
            if isinstance(exc, BrowserEnvironmentBlocked):
                raise
            raise BrowserEnvironmentBlocked("BROWSER_START_FAILED") from exc

    def set_viewport(self, width: int, height: int) -> None:
        self._viewport = BrowserViewport(width, height)
        self._require_page().set_viewport_size({"width": width, "height": height})

    def navigate(self, url: str, *, timeout_ms: int) -> None:
        page = self._require_page()
        if self._navigation_guard is None:
            raise ReferenceAnalysisError("BROWSER_NAVIGATION_GUARD_REQUIRED")
        self._navigation_guard(url)
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        # 只等待有限时间，避免把永不结束的网络空闲当成稳定条件。
        page.wait_for_timeout(min(500, max(1, timeout_ms)))

    def redirect_chain(self) -> Sequence[str]:
        if self._document_urls:
            return tuple(self._document_urls)
        return (str(self._require_page().url),)

    def dom_snapshot(self, *, max_bytes: int) -> Any:
        return self._require_page().evaluate(
            """
            (maxBytes) => ({
              url: location.href,
              title: document.title,
              visible_text: (document.body?.innerText || '').slice(0, maxBytes),
              html: document.documentElement.outerHTML.slice(0, maxBytes)
            })
            """,
            max_bytes,
        )

    def computed_style_snapshot(self, *, max_bytes: int) -> Any:
        return self._require_page().evaluate(
            """
            (maxBytes) => {
              const pick = (element) => {
                if (!element) return null;
                const style = getComputedStyle(element);
                return {
                  display: style.display,
                  position: style.position,
                  color: style.color,
                  backgroundColor: style.backgroundColor,
                  fontFamily: style.fontFamily,
                  fontSize: style.fontSize,
                  lineHeight: style.lineHeight,
                  width: style.width,
                  height: style.height
                };
              };
              return JSON.stringify({
                url: location.href,
                html: pick(document.documentElement),
                body: pick(document.body)
              }).slice(0, maxBytes);
            }
            """,
            max_bytes,
        )

    def screenshot(self, path: Path) -> None:
        self._require_page().screenshot(path=str(path), full_page=True)

    def console_errors(self) -> Sequence[str]:
        return tuple(self._console_errors)

    def failed_requests(self) -> Sequence[str]:
        return tuple(self._failed_requests)

    def runtime_identity(self) -> Mapping[str, Any]:
        browser_version = "unknown"
        if self._browser is not None:
            try:
                browser_version = str(self._browser.version)
            except Exception:
                browser_version = "unknown"
        return {
            "engine": "playwright-chromium",
            "version": browser_version,
            "profile": "temporary-isolated-context",
            "viewport": {"width": self._viewport.width, "height": self._viewport.height},
        }

    def close(self) -> None:
        for resource, method in (
            (self._context, "close"),
            (self._browser, "close"),
        ):
            if resource is not None:
                try:
                    getattr(resource, method)()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None


@dataclass(frozen=True)
class BrowserViewport:
    """受控的单一桌面 viewport；多视口属于后续阶段。"""

    width: int = 1440
    height: int = 900

    def __post_init__(self) -> None:
        if not isinstance(self.width, int) or isinstance(self.width, bool) or not 1 <= self.width <= 4096:
            raise ReferenceAnalysisError("BROWSER_VIEWPORT_INVALID")
        if not isinstance(self.height, int) or isinstance(self.height, bool) or not 1 <= self.height <= 4096:
            raise ReferenceAnalysisError("BROWSER_VIEWPORT_INVALID")


class BrowserAcquisitionProvider:
    """把受控浏览器采集为可哈希的 DOM、样式和截图 artifacts。"""

    provider_id = "browser-web-acquisition"
    version = 1

    def __init__(
        self,
        *,
        adapter_factory: Callable[[], BrowserCaptureAdapter] | None = None,
        resolver: Callable[..., Any] | None = None,
        network_authorizer: Callable[[str], None] | None = None,
        isolation: BrowserIsolationContract | None = None,
        viewport: BrowserViewport | None = None,
        max_dom_bytes: int = 262144,
        max_style_bytes: int = 131072,
        max_screenshot_bytes: int = 10485760,
        timeout_ms: int = 30000,
        allow_cross_origin_redirects: bool = False,
    ) -> None:
        self.adapter_factory = adapter_factory
        self.resolver = resolver
        self.network_authorizer = network_authorizer
        self.isolation = isolation or BrowserIsolationContract()
        self.viewport = viewport or BrowserViewport()
        self.max_dom_bytes = max_dom_bytes
        self.max_style_bytes = max_style_bytes
        self.max_screenshot_bytes = max_screenshot_bytes
        self.timeout_ms = timeout_ms
        self.allow_cross_origin_redirects = allow_cross_origin_redirects
        self.isolation.validate()
        if not all(isinstance(item, int) and item > 0 for item in (max_dom_bytes, max_style_bytes, max_screenshot_bytes, timeout_ms)):
            raise ReferenceAnalysisError("BROWSER_CAPTURE_LIMIT_INVALID")

    @property
    def capability(self) -> ProviderCapability:
        available = (
            self.adapter_factory is not None
            and self.resolver is not None
            and self.network_authorizer is not None
        )
        return ProviderCapability(
            provider_id=self.provider_id,
            version=self.version,
            source_types=("web_page",),
            availability=ProviderAvailability.AVAILABLE if available else ProviderAvailability.BLOCKED_BY_ENVIRONMENT,
            capabilities=("https", "redirect_guard", "dom_snapshot", "computed_style", "screenshot", "console_summary", "network_failure_summary"),
            deterministic=False,
            requires_network=True,
        )

    def _authorize_url(self, url: str) -> str:
        canonical, _ = normalize_public_url(url, allowed_schemes=("https",), resolver=self.resolver)
        if self.network_authorizer is None:
            raise ReferenceAnalysisError("BROWSER_NETWORK_AUTHORIZATION_REQUIRED")
        self.network_authorizer(canonical)
        return canonical

    @staticmethod
    def _same_origin(left: str, right: str) -> bool:
        left_parts = urlsplit(left)
        right_parts = urlsplit(right)
        left_port = left_parts.port or 443
        right_port = right_parts.port or 443
        return (
            left_parts.scheme == right_parts.scheme == "https"
            and left_parts.hostname == right_parts.hostname
            and left_port == right_port
        )

    def _capture_root(self, root: Path) -> Path:
        return ExecutionPathPolicy().assert_module_path(
            "reference_analysis", root, "artifacts/references/acquisitions/browser", operation="write"
        )

    @staticmethod
    def _artifact_from_record(value: Mapping[str, Any]) -> AcquiredArtifact:
        return AcquiredArtifact(
            artifact_ref=str(value["artifact_ref"]),
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            media_type=str(value["media_type"]),
            metadata=dict(value.get("metadata") or {}),
        )

    def _load_existing(self, capture_path: Path, root: Path) -> AcquisitionOutput | None:
        if not capture_path.is_file():
            return None
        try:
            record = yaml.safe_load(capture_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ReferenceAnalysisError("BROWSER_CAPTURE_RECORD_INVALID") from exc
        if not isinstance(record, Mapping):
            raise ReferenceAnalysisError("BROWSER_CAPTURE_RECORD_INVALID")
        assert_valid(validate_browser_capture(record), "browser_capture")
        artifacts = tuple(self._artifact_from_record(item) for item in record["artifacts"])
        for artifact in artifacts:
            path = ExecutionPathPolicy().assert_module_path(
                "reference_analysis", root, artifact.artifact_ref, operation="read"
            )
            if not path.is_file() or _hash_bytes(path.read_bytes()) != artifact.sha256:
                raise ReferenceAnalysisError("BROWSER_CAPTURE_ARTIFACT_HASH_MISMATCH")
        return AcquisitionOutput(
            artifacts=artifacts,
            metadata={
                "capture_id": record["capture_id"],
                "capture_record_ref": capture_path.relative_to(root).as_posix(),
                "canonical_url": record["canonical_url"],
                "redirect_chain": list(record["redirect_chain"]),
                "viewport": dict(record["viewport"]),
                "browser_identity": dict(record["browser_identity"]),
                "captured_at": record["captured_at"],
                "console_errors": list(record["console_errors"]),
                "network_failures": list(record["network_failures"]),
                "trust_level": "untrusted",
            },
            evidence_type="browser_viewport",
        )

    def _write_once(self, path: Path, raw: bytes) -> None:
        if path.exists():
            raise ReferenceAnalysisError("BROWSER_CAPTURE_PARTIAL_ARTIFACTS")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def acquire(
        self,
        request: AcquisitionRequest,
        *,
        root: Path,
        path_policy: ExecutionPathPolicy,
    ) -> AcquisitionOutput:
        if request.source_type != "web_page":
            raise ReferenceAnalysisError("BROWSER_SOURCE_TYPE_UNSUPPORTED")
        uri = request.locator.get("uri")
        if not isinstance(uri, str) or not uri:
            raise ReferenceAnalysisError("WEB_URL_REQUIRED")
        if self.capability.availability is not ProviderAvailability.AVAILABLE:
            raise ReferenceAnalysisError("BROWSER_ACQUISITION_BLOCKED")
        root = Path(root).resolve()
        capture_id = f"CAP-{request.request_fingerprint[:16]}-v{request.snapshot_version}"
        capture_root = path_policy.assert_module_path(
            "reference_analysis", root, "artifacts/references/acquisitions/browser", operation="write"
        )
        capture_path = capture_root / f"{capture_id}.yaml"
        existing = self._load_existing(capture_path, root)
        if existing is not None:
            return existing
        canonical = self._authorize_url(uri)
        adapter = self.adapter_factory() if self.adapter_factory is not None else None
        if adapter is None:
            raise ReferenceAnalysisError("BROWSER_ACQUISITION_BLOCKED")
        started = False
        try:
            adapter.configure_isolation(self.isolation)
            adapter.set_navigation_guard(self._authorize_url)
            adapter.start()
            started = True
            adapter.set_viewport(self.viewport.width, self.viewport.height)
            adapter.navigate(canonical, timeout_ms=self.timeout_ms)
            redirects = tuple(adapter.redirect_chain())
            if not redirects:
                redirects = (canonical,)
            validated_redirects = tuple(validate_redirect_chain(redirects, resolver=self.resolver))
            for target in validated_redirects:
                self._authorize_url(target)
            if not self.allow_cross_origin_redirects and any(
                not self._same_origin(canonical, target) for target in validated_redirects
            ):
                raise ReferenceAnalysisError("BROWSER_CROSS_ORIGIN_REDIRECT_DENIED")
            dom_raw = _safe_json_bytes(adapter.dom_snapshot(max_bytes=self.max_dom_bytes), max_bytes=self.max_dom_bytes)
            style_raw = _safe_json_bytes(
                adapter.computed_style_snapshot(max_bytes=self.max_style_bytes), max_bytes=self.max_style_bytes
            )
            capture_root.mkdir(parents=True, exist_ok=True)
            dom_ref = f"artifacts/references/acquisitions/browser/{capture_id}-dom.json"
            style_ref = f"artifacts/references/acquisitions/browser/{capture_id}-style.json"
            screenshot_ref = f"artifacts/references/acquisitions/browser/{capture_id}-screenshot.png"
            dom_path = path_policy.assert_module_path("reference_analysis", root, dom_ref, operation="write")
            style_path = path_policy.assert_module_path("reference_analysis", root, style_ref, operation="write")
            screenshot_path = path_policy.assert_module_path(
                "reference_analysis", root, screenshot_ref, operation="write"
            )
            self._write_once(dom_path, dom_raw)
            self._write_once(style_path, style_raw)
            if screenshot_path.exists():
                raise ReferenceAnalysisError("BROWSER_CAPTURE_PARTIAL_ARTIFACTS")
            adapter.screenshot(screenshot_path)
            if not screenshot_path.is_file():
                raise ReferenceAnalysisError("BROWSER_SCREENSHOT_MISSING")
            screenshot_raw = screenshot_path.read_bytes()
            if not screenshot_raw or len(screenshot_raw) > self.max_screenshot_bytes:
                raise ReferenceAnalysisError("BROWSER_SCREENSHOT_LIMIT_INVALID")
            if not screenshot_raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ReferenceAnalysisError("BROWSER_SCREENSHOT_FORMAT_INVALID")
            identity = adapter.runtime_identity()
            if not isinstance(identity, Mapping):
                raise ReferenceAnalysisError("BROWSER_RUNTIME_IDENTITY_INVALID")
            artifacts = (
                AcquiredArtifact(dom_ref, _hash_bytes(dom_raw), len(dom_raw), "application/json", {"kind": "dom_snapshot", "bounded": True}),
                AcquiredArtifact(style_ref, _hash_bytes(style_raw), len(style_raw), "application/json", {"kind": "computed_style", "bounded": True}),
                AcquiredArtifact(screenshot_ref, _hash_bytes(screenshot_raw), len(screenshot_raw), "image/png", {"kind": "screenshot", "viewport": {"width": self.viewport.width, "height": self.viewport.height}}),
            )
            record = {
                "schema_version": 1,
                "capture_id": capture_id,
                "reference_id": request.reference_id,
                "canonical_url": canonical,
                "redirect_chain": list(validated_redirects),
                "viewport": {"width": self.viewport.width, "height": self.viewport.height},
                "browser_identity": {key: safe_text(value, limit=256) for key, value in identity.items() if isinstance(key, str)},
                "captured_at": _now(),
                "artifacts": [
                    {
                        "artifact_ref": item.artifact_ref,
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "media_type": item.media_type,
                        "metadata": dict(item.metadata),
                    }
                    for item in artifacts
                ],
                "console_errors": list(_safe_string_list(adapter.console_errors(), limit=64)),
                "network_failures": list(_safe_string_list(adapter.failed_requests(), limit=64, url_values=True)),
                "trust_level": "untrusted",
            }
            assert_valid(validate_browser_capture(record), "browser_capture")
            self._write_once(capture_path, yaml.safe_dump(record, allow_unicode=True, sort_keys=False).encode("utf-8"))
            return AcquisitionOutput(
                artifacts=artifacts,
                metadata={
                    "capture_id": capture_id,
                    "capture_record_ref": capture_path.relative_to(root).as_posix(),
                    "canonical_url": canonical,
                    "redirect_chain": list(validated_redirects),
                    "viewport": {"width": self.viewport.width, "height": self.viewport.height},
                    "browser_identity": dict(record["browser_identity"]),
                    "captured_at": record["captured_at"],
                    "console_errors": list(record["console_errors"]),
                    "network_failures": list(record["network_failures"]),
                    "trust_level": "untrusted",
                },
                evidence_type="browser_viewport",
            )
        except BrowserEnvironmentBlocked as exc:
            raise ReferenceAnalysisError("BROWSER_ACQUISITION_BLOCKED") from exc
        except ReferenceAnalysisError:
            raise
        except Exception as exc:
            raise ReferenceAnalysisError("BROWSER_ACQUISITION_FAILED") from exc
        finally:
            if started:
                try:
                    adapter.close()
                except Exception:
                    pass
