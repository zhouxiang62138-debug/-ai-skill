"""运行时错误分类。"""


class RuntimeErrorBase(RuntimeError):
    """所有 F10 运行时错误的基类。"""


class RuntimeValidationError(RuntimeErrorBase):
    """输入或持久化数据不满足确定性约束。"""


class RuntimeStorageError(RuntimeErrorBase):
    """Session Store 无法安全完成操作。"""


class StateConflictError(RuntimeErrorBase):
    """业务状态 revision 或 hash 发生冲突。"""


class LeaseError(RuntimeErrorBase):
    """Worker 未持有有效租约或租约版本不匹配。"""


class RecoveryError(RuntimeErrorBase):
    """运行时状态无法自动恢复。"""


class ProjectMissingError(RecoveryError):
    """Session 引用的项目目录或 project.yaml 缺失。"""
