"""Browser Profile 与 Evidence 的结构化数据模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from .errors import BrowserPolicyError


_EVALUATION_ID = re.compile(r"^evaluation-[0-9]{3}$")
_BROWSER_RUN_ID = re.compile(r"^browser-run-[0-9]{3}$")
_BROWSER_STEP_ID = re.compile(r"^browser-run-[0-9]{3}-step-[0-9]{3}$")
_SAFE_ACTIONS = frozenset(
    {
        "start",
        "navigate",
        "click",
        "fill",
        "select",
        "keyboard",
        "wait_for_selector",
        "wait_for_state",
        "read_visible_text",
        "inspect_dom_state",
        "inspect_url",
        "screenshot",
        "close",
    }
)
_SAFE_TEXT_PATTERN = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+"
    r"|bearer\s+[A-Za-z0-9._~+/=-]+"
)


def utc_now() -> str:
    """返回带时区的当前时间。"""

    return datetime.now(timezone.utc).isoformat()


def safe_text(value: object, *, limit: int = 4000) -> str:
    """对进入 Evidence 的文本做限长和敏感字段脱敏。"""

    text = str(value)
    text = _SAFE_TEXT_PATTERN.sub("[REDACTED]", text)
    return text if len(text) <= limit else text[:limit] + "...[TRUNCATED]"


def safe_url(value: object) -> str:
    """只保留 URL 的非敏感部分，不把 query/fragment 写入 Evidence。"""

    parsed = urlparse(str(value))
    if not parsed.scheme or not parsed.netloc:
        return safe_text(value, limit=512)
    path = parsed.path or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrowserPolicyError("BROWSER_VALUE_INVALID", name)
    return value


@dataclass(frozen=True)
class BrowserProfile:
    """由 evaluation profile 提供的 Browser 验收设置。"""

    required: bool
    base_url: str | None = None
    startup_command: tuple[str, ...] = ()
    timeout_seconds: float = 30.0
    screenshot_policy: str = "on_failure"
    console_error_policy: str = "fail_on_error"
    network_failure_policy: str = "fail_on_failure"
    required_scenarios: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BrowserProfile":
        data = value.get("browser_validation", value)
        if not isinstance(data, Mapping):
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID")
        required = data.get("required", False)
        if not isinstance(required, bool):
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "required")
        base_url = data.get("base_url")
        if base_url is not None and (
            not isinstance(base_url, str) or not base_url.strip()
        ):
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "base_url")
        command = data.get("startup_command", [])
        if not isinstance(command, list) or not all(
            isinstance(item, str) and item for item in command
        ):
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "startup_command")
        timeout = data.get("timeout_seconds", 30.0)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "timeout_seconds")
        scenario_ids = data.get("required_scenarios", [])
        if not isinstance(scenario_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in scenario_ids
        ):
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "required_scenarios")
        values = {
            "screenshot_policy": data.get("screenshot_policy", "on_failure"),
            "console_error_policy": data.get("console_error_policy", "fail_on_error"),
            "network_failure_policy": data.get(
                "network_failure_policy", "fail_on_failure"
            ),
        }
        allowed_policies = {
            "screenshot_policy": {"never", "on_failure", "always"},
            "console_error_policy": {"ignore", "record", "fail_on_error"},
            "network_failure_policy": {"ignore", "record", "fail_on_failure"},
        }
        for name, current in values.items():
            if current not in allowed_policies[name]:
                raise BrowserPolicyError("BROWSER_PROFILE_INVALID", name)
        return cls(
            required=required,
            base_url=base_url,
            startup_command=tuple(command),
            timeout_seconds=float(timeout),
            screenshot_policy=str(values["screenshot_policy"]),
            console_error_policy=str(values["console_error_policy"]),
            network_failure_policy=str(values["network_failure_policy"]),
            required_scenarios=tuple(scenario_ids),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.required, bool):
            raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "required")
        if self.base_url is not None:
            parsed = urlparse(self.base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise BrowserPolicyError("BROWSER_PROFILE_INVALID", "base_url")
            if parsed.username or parsed.password:
                raise BrowserPolicyError("BROWSER_PROFILE_SECRET_FORBIDDEN")


@dataclass(frozen=True)
class BrowserStepRecord:
    """一次不可变 Browser 操作证据。"""

    evaluation_id: str
    requirement_id: str
    acceptance_criterion_id: str
    browser_run_id: str
    step_id: str
    action: str
    target: str
    expected: str
    observed: str
    result: str
    screenshot_reference: str | None
    console_errors: tuple[str, ...]
    network_failures: tuple[str, ...]
    started_at: str
    finished_at: str

    def __post_init__(self) -> None:
        for name in (
            "evaluation_id",
            "requirement_id",
            "acceptance_criterion_id",
            "browser_run_id",
            "step_id",
            "action",
            "target",
            "expected",
            "observed",
            "result",
            "started_at",
            "finished_at",
        ):
            _required_text(getattr(self, name), name)
        if not _EVALUATION_ID.fullmatch(self.evaluation_id):
            raise BrowserPolicyError("BROWSER_EVIDENCE_INVALID", "evaluation_id")
        if not _BROWSER_RUN_ID.fullmatch(self.browser_run_id):
            raise BrowserPolicyError("BROWSER_EVIDENCE_INVALID", "browser_run_id")
        if not _BROWSER_STEP_ID.fullmatch(self.step_id):
            raise BrowserPolicyError("BROWSER_EVIDENCE_INVALID", "step_id")
        if self.action not in _SAFE_ACTIONS:
            raise BrowserPolicyError("BROWSER_EVIDENCE_INVALID", "action")
        if self.result not in {"PASS", "FAIL", "BLOCKED"}:
            raise BrowserPolicyError("BROWSER_EVIDENCE_INVALID", "result")
        if self.screenshot_reference is not None and not isinstance(
            self.screenshot_reference, str
        ):
            raise BrowserPolicyError("BROWSER_EVIDENCE_INVALID", "screenshot_reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "requirement_id": self.requirement_id,
            "acceptance_criterion_id": self.acceptance_criterion_id,
            "browser_run_id": self.browser_run_id,
            "step_id": self.step_id,
            "action": self.action,
            "target": self.target,
            "expected": self.expected,
            "observed": self.observed,
            "result": self.result,
            "screenshot_reference": self.screenshot_reference,
            "console_errors": list(self.console_errors),
            "network_failures": list(self.network_failures),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True)
class BrowserRunRecord:
    """一次 Browser Run 的摘要证据。"""

    evaluation_id: str
    browser_run_id: str
    scenario_id: str | None
    result: str
    failure_class: str | None
    started_at: str
    finished_at: str
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "browser_run_id": self.browser_run_id,
            "scenario_id": self.scenario_id,
            "result": self.result,
            "failure_class": self.failure_class,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "evidence_refs": list(self.evidence_refs),
        }


def browser_actions() -> frozenset[str]:
    """返回第一版允许的确定性 Browser 动作。"""

    return _SAFE_ACTIONS
