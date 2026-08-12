"""F14-C4 Enforced Coverage Gate。

该模块只负责在模型调用前做覆盖门禁；C4 阶段仍交付完整 F13 Context，不执行裁剪。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from runtime.errors import RuntimeValidationError

from .shadow import ShadowComparison, ShadowCoverageGate


@dataclass(frozen=True)
class EnforcementDecision:
    result: str
    allow_model_invocation: bool
    context_mode: str
    reasons: tuple[str, ...]
    fallback_to_f13: bool


class EnforcedCoverageGate:
    """将 C3 结果转换为 C4 的调用前决策。"""

    def enforce(self, comparison: ShadowComparison) -> EnforcementDecision:
        shadow = ShadowCoverageGate().evaluate(comparison)
        if shadow.passed:
            return EnforcementDecision(
                result="ALLOW",
                allow_model_invocation=True,
                context_mode="F13_FULL",
                reasons=(),
                fallback_to_f13=False,
            )
        return EnforcementDecision(
            result="BLOCK",
            allow_model_invocation=False,
            context_mode="F13_FULL_BLOCKED",
            reasons=shadow.reasons,
            fallback_to_f13=False,
        )

    def invoke_with_fallback(
        self,
        current_context: Any,
        comparison_factory: Callable[[], ShadowComparison],
        model_call: Callable[[Any], Any],
    ) -> tuple[EnforcementDecision, Any]:
        """覆盖门失败时阻断；优化器崩溃时安全回退到现有 F13 Context。"""

        try:
            comparison = comparison_factory()
            decision = self.enforce(comparison)
        except Exception as exc:
            decision = EnforcementDecision(
                result="FALLBACK_F13",
                allow_model_invocation=True,
                context_mode="F13_FULL",
                reasons=("optimization_unavailable", type(exc).__name__),
                fallback_to_f13=True,
            )
            return decision, model_call(current_context)
        if not decision.allow_model_invocation:
            raise RuntimeValidationError("CONTEXT_COVERAGE_BLOCKED")
        # C4 即使通过，也只验证覆盖，不把候选 Context 交给模型。
        return decision, model_call(current_context)


__all__ = ["EnforcedCoverageGate", "EnforcementDecision"]
