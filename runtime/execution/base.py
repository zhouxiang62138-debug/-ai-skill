"""F11.1 ExecutionEnvironment Contract。

本文件只定义 Brain 与 Hands 的边界，不实现 Local 或 Docker。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ExecutionContext, ExecutionRequest, ExecutionResult


class ExecutionEnvironment(ABC):
    """不持有控制面、租约、数据库连接或事件写入能力的执行后端。"""

    @abstractmethod
    def provision(self, context: ExecutionContext) -> None:
        """准备执行环境。"""

    @abstractmethod
    def execute(
        self, context: ExecutionContext, request: ExecutionRequest
    ) -> ExecutionResult:
        """执行参数数组，不接受 Shell command string。"""

    @abstractmethod
    def read_file(self, context: ExecutionContext, path: str) -> str:
        """读取项目内文件。"""

    @abstractmethod
    def write_file(self, context: ExecutionContext, path: str, content: str) -> None:
        """写入项目内文件；权限由 Broker 先行校验。"""

    @abstractmethod
    def list_files(self, context: ExecutionContext, path: str = ".") -> list[str]:
        """列出项目内文件。"""

    @abstractmethod
    def snapshot(self, context: ExecutionContext) -> str:
        """返回后端定义的快照标识；正式快照实现留给后续阶段。"""

    @abstractmethod
    def restore(self, context: ExecutionContext, snapshot_id: str) -> None:
        """恢复快照；正式恢复实现留给后续阶段。"""

    @abstractmethod
    def terminate(self, context: ExecutionContext) -> None:
        """终止执行环境。"""
