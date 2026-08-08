"""Browser Driver 协议和可选的确定性 Playwright 适配器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .errors import BrowserEnvironmentBlocked


class BrowserAdapter(Protocol):
    """Harness 所需的最小 Driver 接口；测试可注入确定性实现。"""

    def start(self) -> None: ...

    def navigate(self, url: str, *, timeout_ms: int) -> None: ...

    def click(self, selector: str, *, timeout_ms: int) -> None: ...

    def fill(self, selector: str, value: str, *, timeout_ms: int) -> None: ...

    def select(self, selector: str, value: str, *, timeout_ms: int) -> None: ...

    def keyboard(self, key: str) -> None: ...

    def wait_for_selector(self, selector: str, *, timeout_ms: int) -> None: ...

    def wait_for_state(self, selector: str, state: str, *, timeout_ms: int) -> None: ...

    def read_visible_text(self, selector: str, *, timeout_ms: int) -> str: ...

    def inspect_dom_state(self, selector: str, *, timeout_ms: int) -> dict[str, Any]: ...

    def inspect_url(self) -> str: ...

    def screenshot(self, path: Path) -> None: ...

    def console_errors(self) -> list[str]: ...

    def failed_requests(self) -> list[str]: ...

    def close(self) -> None: ...


class PlaywrightBrowserAdapter:
    """Playwright sync API 适配器，依赖在真正启动时才导入。"""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._console_errors: list[str] = []
        self._failed_requests: list[str] = []

    def _require_page(self) -> Any:
        if self._page is None:
            raise BrowserEnvironmentBlocked("BROWSER_NOT_STARTED")
        return self._page

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserEnvironmentBlocked("BROWSER_PLAYWRIGHT_UNAVAILABLE") from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._page = self._browser.new_page()
            self._page.on(
                "console",
                lambda message: self._console_errors.append(message.text)
                if message.type == "error" and len(self._console_errors) < 64
                else None,
            )
            self._page.on(
                "requestfailed",
                lambda request: self._failed_requests.append(request.url)
                if len(self._failed_requests) < 64
                else None,
            )
        except Exception as exc:
            self.close()
            raise BrowserEnvironmentBlocked("BROWSER_START_FAILED") from exc

    def navigate(self, url: str, *, timeout_ms: int) -> None:
        self._require_page().goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def click(self, selector: str, *, timeout_ms: int) -> None:
        self._require_page().locator(selector).click(timeout=timeout_ms)

    def fill(self, selector: str, value: str, *, timeout_ms: int) -> None:
        self._require_page().locator(selector).fill(value, timeout=timeout_ms)

    def select(self, selector: str, value: str, *, timeout_ms: int) -> None:
        self._require_page().locator(selector).select_option(value=value, timeout=timeout_ms)

    def keyboard(self, key: str) -> None:
        self._require_page().keyboard.press(key)

    def wait_for_selector(self, selector: str, *, timeout_ms: int) -> None:
        self._require_page().locator(selector).wait_for(state="visible", timeout=timeout_ms)

    def wait_for_state(self, selector: str, state: str, *, timeout_ms: int) -> None:
        self._require_page().locator(selector).wait_for(state=state, timeout=timeout_ms)

    def read_visible_text(self, selector: str, *, timeout_ms: int) -> str:
        locator = self._require_page().locator(selector)
        locator.wait_for(state="visible", timeout=timeout_ms)
        return locator.inner_text(timeout=timeout_ms)

    def inspect_dom_state(self, selector: str, *, timeout_ms: int) -> dict[str, Any]:
        locator = self._require_page().locator(selector)
        count = locator.count()
        if count == 0:
            return {"count": 0, "visible": False, "text": ""}
        return {
            "count": count,
            "visible": locator.first.is_visible(timeout=timeout_ms),
            "text": locator.first.inner_text(timeout=timeout_ms),
        }

    def inspect_url(self) -> str:
        return str(self._require_page().url)

    def screenshot(self, path: Path) -> None:
        self._require_page().screenshot(path=str(path), full_page=True)

    def console_errors(self) -> list[str]:
        return list(self._console_errors)

    def failed_requests(self) -> list[str]:
        return list(self._failed_requests)

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None
        self._page = None
