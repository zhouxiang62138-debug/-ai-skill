"""First-Ask Intake Module 的可执行入口。"""

from .first_ask import (
    FirstAskIntakeModule,
    FirstAskResult,
    ReferenceCandidate,
    detect_references,
)

__all__ = [
    "FirstAskIntakeModule",
    "FirstAskResult",
    "ReferenceCandidate",
    "detect_references",
]
