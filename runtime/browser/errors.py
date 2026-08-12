"""Browser Harness 的稳定错误分类。"""

from runtime.errors import RuntimeErrorBase


class BrowserError(RuntimeErrorBase):
    """Browser 操作或策略错误。"""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        message = code if not detail else f"{code}: {detail}"
        super().__init__(message)


class BrowserPolicyError(BrowserError):
    """Browser Policy 或 Profile 不允许请求。"""


class BrowserEnvironmentBlocked(BrowserError):
    """Browser 依赖、启动环境或运行环境不可用。"""


class BrowserActionFailed(BrowserError):
    """应用行为或确定性 Browser 操作失败。"""
