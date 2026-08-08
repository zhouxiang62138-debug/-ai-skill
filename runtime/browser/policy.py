"""Browser Capability 与 Browser Policy 的确定性门禁。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from scripts.project_state import parse_project_yaml

from runtime.policy import CapabilityPolicy

from .errors import BrowserPolicyError
from .models import BrowserProfile, browser_actions


_ROOT = Path(__file__).resolve().parents[2]


def _load_policy(path: str | Path | None) -> dict[str, Any]:
    target = Path(path) if path else _ROOT / "config" / "browser_policy.yaml"
    try:
        raw = parse_project_yaml(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise BrowserPolicyError("BROWSER_POLICY_INVALID") from exc
    if not isinstance(raw, dict):
        raise BrowserPolicyError("BROWSER_POLICY_INVALID")
    return raw


class BrowserPolicy:
    """通过配置和 F12 Capability 控制 Browser Harness。"""

    def __init__(
        self,
        config_path: str | Path | None = None,
        *,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        document = _load_policy(config_path)
        if document.get("default") != "deny":
            raise BrowserPolicyError("BROWSER_POLICY_INVALID", "default")
        if document.get("capability") != "browser.access":
            raise BrowserPolicyError("BROWSER_POLICY_INVALID", "capability")
        roles = document.get("roles")
        if not isinstance(roles, dict):
            raise BrowserPolicyError("BROWSER_POLICY_INVALID", "roles")
        actions = document.get("actions")
        if not isinstance(actions, list) or not set(actions).issubset(browser_actions()):
            raise BrowserPolicyError("BROWSER_POLICY_INVALID", "actions")
        self._roles = roles
        self._actions = frozenset(actions)
        self._capability_policy = capability_policy or CapabilityPolicy()

    def authorize(self, role: str, profile: Mapping[str, Any]) -> BrowserProfile:
        """先检查 Profile，再通过 F12 Capability；默认拒绝。"""

        parsed = BrowserProfile.from_mapping(profile)
        if not parsed.required:
            raise BrowserPolicyError("BROWSER_PROFILE_NOT_REQUIRED")
        if parsed.base_url is None:
            raise BrowserPolicyError("BROWSER_PROFILE_BASE_URL_REQUIRED")
        role_policy = self._roles.get(role)
        if not isinstance(role_policy, Mapping) or role_policy.get("allowed") is not True:
            raise BrowserPolicyError("BROWSER_ROLE_DENIED", role)
        self._capability_policy.authorize(
            role,
            "browser.access",
            resource=parsed.base_url,
            action="browser.acceptance",
        )
        return parsed

    def authorize_action(self, action: str) -> None:
        if action not in self._actions:
            raise BrowserPolicyError("BROWSER_ACTION_DENIED", action)

    @staticmethod
    def assert_url(url: str, base_url: str) -> None:
        """只允许访问 Profile base URL 的同源地址。"""

        target = urlparse(url)
        base = urlparse(base_url)
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise BrowserPolicyError("BROWSER_URL_INVALID")
        if target.username or target.password:
            raise BrowserPolicyError("BROWSER_URL_SECRET_FORBIDDEN")
        target_port = target.port or (443 if target.scheme == "https" else 80)
        base_port = base.port or (443 if base.scheme == "https" else 80)
        if (
            target.scheme != base.scheme
            or target.hostname.casefold() != (base.hostname or "").casefold()
            or target_port != base_port
        ):
            raise BrowserPolicyError("BROWSER_URL_OUTSIDE_BASE_ORIGIN")
