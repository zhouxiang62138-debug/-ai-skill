"""确定性的 Browser 操作与逐步 Evidence 记录。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .adapter import BrowserAdapter, PlaywrightBrowserAdapter
from .errors import (
    BrowserActionFailed,
    BrowserEnvironmentBlocked,
    BrowserError,
    BrowserPolicyError,
)
from .models import (
    BrowserProfile,
    BrowserRunRecord,
    BrowserStepRecord,
    safe_text,
    safe_url,
    utc_now,
)
from .policy import BrowserPolicy


class BrowserHarness:
    """提供最小稳定操作集，并把每一步变成可复现 Evidence。"""

    def __init__(
        self,
        *,
        policy: BrowserPolicy,
        profile: BrowserProfile,
        evaluation_id: str,
        browser_run_id: str,
        requirement_id: str,
        acceptance_criterion_id: str,
        scenario_id: str | None = None,
        project_root: str | Path | None = None,
        adapter: BrowserAdapter | None = None,
    ) -> None:
        self._policy = policy
        self._profile = profile
        self._evaluation_id = evaluation_id
        self._browser_run_id = browser_run_id
        self._requirement_id = requirement_id
        self._acceptance_criterion_id = acceptance_criterion_id
        self._scenario_id = scenario_id
        self._project_root = Path(project_root).resolve() if project_root else None
        self._adapter = adapter or PlaywrightBrowserAdapter()
        self._steps: list[BrowserStepRecord] = []
        self._started_at: str | None = None
        self._finished_at: str | None = None
        self._environment_blocked = False
        self._closed = False

    @property
    def steps(self) -> tuple[BrowserStepRecord, ...]:
        return tuple(self._steps)

    def _timeout_ms(self) -> int:
        return max(1, int(self._profile.timeout_seconds * 1000))

    def _next_step_id(self) -> str:
        return f"{self._browser_run_id}-step-{len(self._steps) + 1:03d}"

    def _driver_diagnostics(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        try:
            console_errors = tuple(
                safe_text(item, limit=1000) for item in self._adapter.console_errors()
            )
        except Exception:
            console_errors = ()
        try:
            network_failures = tuple(safe_url(item) for item in self._adapter.failed_requests())
        except Exception:
            network_failures = ()
        return console_errors, network_failures

    def _observe(self, value: Any) -> str:
        if isinstance(value, dict):
            return safe_text(
                {key: safe_text(item, limit=1000) for key, item in value.items()},
                limit=4000,
            )
        if isinstance(value, (list, tuple, set)):
            return safe_text([safe_text(item, limit=1000) for item in value])
        return safe_text(value)

    def _action(
        self,
        action: str,
        target: str,
        expected: str,
        operation: Callable[[], Any],
        *,
        screenshot_reference: str | None = None,
    ) -> Any:
        self._policy.authorize_action(action)
        started_at = utc_now()
        result = "PASS"
        observed = ""
        failure: BrowserError | None = None
        try:
            value = operation()
            observed = self._observe(value)
        except BrowserEnvironmentBlocked as exc:
            self._environment_blocked = True
            result = "BLOCKED"
            observed = str(exc)
            failure = exc
            value = None
        except Exception as exc:
            result = "FAIL"
            observed = f"{type(exc).__name__}: {safe_text(exc)}"
            failure = BrowserActionFailed("BROWSER_ACTION_FAILED", observed)
            value = None
        console_errors, network_failures = self._driver_diagnostics()
        if result == "PASS" and self._profile.console_error_policy == "fail_on_error" and console_errors:
            result = "FAIL"
            observed = "console error captured"
            failure = BrowserActionFailed("BROWSER_CONSOLE_ERROR")
        if result == "PASS" and self._profile.network_failure_policy == "fail_on_failure" and network_failures:
            result = "FAIL"
            observed = "network failure captured"
            failure = BrowserActionFailed("BROWSER_NETWORK_FAILURE")
        finished_at = utc_now()
        self._steps.append(
            BrowserStepRecord(
                evaluation_id=self._evaluation_id,
                requirement_id=self._requirement_id,
                acceptance_criterion_id=self._acceptance_criterion_id,
                browser_run_id=self._browser_run_id,
                step_id=self._next_step_id(),
                action=action,
                target=safe_text(target, limit=1000),
                expected=safe_text(expected, limit=2000),
                observed=observed,
                result=result,
                screenshot_reference=screenshot_reference,
                console_errors=console_errors,
                network_failures=network_failures,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        if failure is not None:
            raise failure
        return value

    def start(self) -> None:
        if self._started_at is None:
            self._started_at = utc_now()
        self._action("start", "browser", "browser starts", self._adapter.start)

    def navigate(self, url: str) -> None:
        if self._profile.base_url is None:
            raise BrowserPolicyError("BROWSER_PROFILE_BASE_URL_REQUIRED")
        self._policy.assert_url(url, self._profile.base_url)
        self._action(
            "navigate",
            safe_url(url),
            "page navigates to the same-origin URL",
            lambda: self._adapter.navigate(url, timeout_ms=self._timeout_ms()),
        )

    def click(self, selector: str) -> None:
        self._action(
            "click",
            selector,
            "target is clicked",
            lambda: self._adapter.click(selector, timeout_ms=self._timeout_ms()),
        )

    def fill(self, selector: str, value: str) -> None:
        """填写值但不把值写进 Evidence，避免密码或业务 Secret 泄漏。"""

        self._action(
            "fill",
            selector,
            "target is filled",
            lambda: self._adapter.fill(selector, value, timeout_ms=self._timeout_ms()),
        )

    def select(self, selector: str, value: str) -> None:
        self._action(
            "select",
            selector,
            "option is selected",
            lambda: self._adapter.select(selector, value, timeout_ms=self._timeout_ms()),
        )

    def keyboard(self, key: str) -> None:
        self._action(
            "keyboard",
            key,
            "keyboard input is accepted",
            lambda: self._adapter.keyboard(key),
        )

    def wait_for_selector(self, selector: str) -> None:
        self._action(
            "wait_for_selector",
            selector,
            "selector becomes visible",
            lambda: self._adapter.wait_for_selector(selector, timeout_ms=self._timeout_ms()),
        )

    def wait_for_state(self, selector: str, state: str) -> None:
        self._action(
            "wait_for_state",
            selector,
            f"selector reaches state {state}",
            lambda: self._adapter.wait_for_state(
                selector, state, timeout_ms=self._timeout_ms()
            ),
        )

    def read_visible_text(self, selector: str) -> str:
        value = self._action(
            "read_visible_text",
            selector,
            "visible text is readable",
            lambda: self._adapter.read_visible_text(selector, timeout_ms=self._timeout_ms()),
        )
        return safe_text(value)

    def inspect_dom_state(self, selector: str) -> dict[str, Any]:
        value = self._action(
            "inspect_dom_state",
            selector,
            "DOM state is inspectable",
            lambda: self._adapter.inspect_dom_state(selector, timeout_ms=self._timeout_ms()),
        )
        return value if isinstance(value, dict) else {"value": safe_text(value)}

    def inspect_url(self) -> str:
        value = self._action(
            "inspect_url", "page", "current URL is readable", self._adapter.inspect_url
        )
        return safe_url(value)

    def _screenshot_path(self, reference: str) -> tuple[Path, str]:
        if self._project_root is None:
            raise BrowserPolicyError("BROWSER_SCREENSHOT_ROOT_REQUIRED")
        candidate = Path(reference)
        if candidate.is_absolute():
            raise BrowserPolicyError("BROWSER_SCREENSHOT_PATH_INVALID")
        target = (self._project_root / candidate).resolve()
        try:
            target.relative_to(self._project_root)
        except ValueError as exc:
            raise BrowserPolicyError("BROWSER_SCREENSHOT_PATH_INVALID") from exc
        return target, target.relative_to(self._project_root).as_posix()

    def screenshot(self, reference: str) -> str:
        target, relative = self._screenshot_path(reference)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._action(
            "screenshot",
            relative,
            "screenshot is captured",
            lambda: self._adapter.screenshot(target),
            screenshot_reference=relative,
        )
        return relative

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._action("close", "browser", "browser closes", self._adapter.close)
        finally:
            self._closed = True
            self._finished_at = utc_now()

    def run_record(self) -> BrowserRunRecord:
        started_at = self._started_at or utc_now()
        finished_at = self._finished_at or utc_now()
        if self._environment_blocked or any(step.result == "BLOCKED" for step in self._steps):
            result = "BLOCKED"
            failure_class = "evaluation_environment_blocked"
        elif any(step.result == "FAIL" for step in self._steps):
            result = "FAIL"
            failure_class = "implementation_failure"
        else:
            result = "PASS"
            failure_class = None
        return BrowserRunRecord(
            evaluation_id=self._evaluation_id,
            browser_run_id=self._browser_run_id,
            scenario_id=self._scenario_id,
            result=result,
            failure_class=failure_class,
            started_at=started_at,
            finished_at=finished_at,
            evidence_refs=tuple(step.step_id for step in self._steps),
        )

    def evidence_bundle(self) -> dict[str, list[dict[str, Any]]]:
        """返回可合并进现有 Evidence Manifest 的只读数据。"""

        return {
            "browser_runs": [self.run_record().to_dict()],
            "browser_evidence": [step.to_dict() for step in self._steps],
        }
