"""F12：凭据、能力、网络与注入输入的确定性安全边界。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Mapping

from .errors import RuntimeValidationError


_SECRET = re.compile(r"(?i)(api[_-]?key|token|secret|password|private[_-]?key)\s*[:=]")
_INJECTION = re.compile(r"(?i)(ignore (all |previous )?instructions|system prompt|reveal .*secret|jailbreak)")


@dataclass(frozen=True)
class CapabilityPolicy:
    """角色到工具和网络域名的显式最小权限映射。"""
    role_tools: Mapping[str, frozenset[str]]
    tool_domains: Mapping[str, frozenset[str]]

    def authorize(self, role: str, tool: str, domain: str | None = None) -> None:
        if tool not in self.role_tools.get(role, frozenset()):
            raise RuntimeValidationError("角色未获该工具能力")
        if domain is not None and domain not in self.tool_domains.get(tool, frozenset()):
            raise RuntimeValidationError("工具未获该网络域名能力")


class CredentialProxy:
    """凭据只留在代理 handler；不注入执行环境或 Event Payload。"""
    def __init__(self, policy: CapabilityPolicy) -> None:
        self.policy = policy
        self._handlers: dict[str, Callable[[dict[str, str]], dict[str, str]]] = {}

    def register(self, tool: str, handler: Callable[[dict[str, str]], dict[str, str]]) -> None:
        self._handlers[tool] = handler

    def invoke(self, role: str, tool: str, request: dict[str, str], *, domain: str | None = None) -> dict[str, str]:
        self.policy.authorize(role, tool, domain)
        if tool not in self._handlers or _SECRET.search(" ".join(f"{k}={v}" for k, v in request.items())):
            raise RuntimeValidationError("代理请求含敏感字段或工具未注册")
        result = self._handlers[tool](dict(request))
        if _SECRET.search(" ".join(f"{k}={v}" for k, v in result.items())):
            raise RuntimeValidationError("代理响应不得返回凭据")
        return result


def sanitize_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """移除可能是凭据的环境变量；执行环境默认不继承它们。"""
    return {key: value for key, value in environment.items() if not _SECRET.search(f"{key}=")}


def reject_prompt_injection(text: str) -> None:
    """拒绝明显要求越权、泄露凭据或改写系统指令的外部文本。"""
    if _INJECTION.search(text):
        raise RuntimeValidationError("检测到疑似 Prompt Injection")


def scan_text_for_secrets(text: str) -> list[str]:
    """返回疑似敏感字段名，供文件与日志扫描使用。"""
    return sorted({match.group(1).lower() for match in _SECRET.finditer(text)})
