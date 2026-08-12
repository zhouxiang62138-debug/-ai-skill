"""Reference Analysis 的稳定错误码。"""


class ReferenceAnalysisError(RuntimeError):
    """模块拒绝继续处理时抛出的可审计错误。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class ReferenceArtifactExistsError(ReferenceAnalysisError):
    """目标工件已经存在，追加式存储拒绝覆盖。"""


class ReferenceAnalysisCrash(ReferenceAnalysisError):
    """仅用于验证崩溃恢复边界的可控故障。"""
