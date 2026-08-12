"""F14 资格阶梯、全局门禁与 F13 紧急回退的回归测试。"""

import unittest

from runtime.errors import RuntimeValidationError
from runtime.f14_control import F13_FULL, F14_SELECTIVE, F14FeatureFlags, F14RolloutPolicy
from runtime.invocation_gate import InvocationGate


class F14RolloutGovernanceTests(unittest.TestCase):
    """验证 rollout 状态机不会把受控资格误当成全局资格。"""

    def test_runtime_default_is_implemented_and_f13(self) -> None:
        flags = F14FeatureFlags()
        self.assertEqual(flags.rollout.qualification_status, "IMPLEMENTED")
        self.assertEqual(flags.delivery_for(role="generator", phase="implementation"), F13_FULL)

    def test_promotion_requires_the_next_level_evidence(self) -> None:
        policy = F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED")
        self.assertFalse(policy.can_transition("GLOBAL_READY", {"controlled": "PASS"}))
        self.assertTrue(
            policy.can_transition(
                "REAL_MODEL_QUALIFIED",
                {"controlled": "PASS", "real_model": "PASS"},
            )
        )
        with self.assertRaises(RuntimeValidationError):
            policy.assert_transition("GLOBAL_ENABLED", {"controlled": "PASS"})

    def test_global_enabled_requires_qualified_state(self) -> None:
        with self.assertRaises(RuntimeValidationError):
            F14RolloutPolicy(
                mode="global",
                qualification_status="CONTROLLED_QUALIFIED",
                global_enabled=True,
            )

    def test_kill_switch_and_overrides_force_f13_delivery(self) -> None:
        flags = F14FeatureFlags(
            context_delivery_mode=F14_SELECTIVE,
            selective_context_enabled=True,
            selective_roles=("generator",),
            selective_phases=("implementation",),
            qualification_evidence={"controlled": "PASS"},
            rollout=F14RolloutPolicy(
                qualification_status="CONTROLLED_QUALIFIED",
                kill_switch_active=True,
                disabled_projects=("project-1",),
            ),
        )
        self.assertEqual(
            flags.delivery_for(
                role="generator",
                phase="implementation",
                project_id="project-1",
                test_project=True,
            ),
            F13_FULL,
        )

    def test_rollback_returns_f13_safe_state(self) -> None:
        flags = F14FeatureFlags(
            context_delivery_mode=F14_SELECTIVE,
            selective_context_enabled=True,
            selective_roles=("generator",),
            selective_phases=("implementation",),
            qualification_evidence={"controlled": "PASS"},
            rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
        )
        rolled_back = flags.rollback()
        self.assertEqual(rolled_back.rollout.qualification_status, "FALLBACK_F13")
        self.assertFalse(rolled_back.selective_context_enabled)
        self.assertEqual(
            rolled_back.delivery_for(role="generator", phase="implementation", test_project=True),
            F13_FULL,
        )

    def test_formal_config_keeps_invocation_gate_closed(self) -> None:
        self.assertFalse(InvocationGate.from_f14_config().enabled)

    def test_selective_context_denies_missing_controlled_evidence(self) -> None:
        flags = F14FeatureFlags(
            context_delivery_mode=F14_SELECTIVE,
            selective_context_enabled=True,
            selective_roles=("generator",),
            selective_phases=("implementation",),
            qualification_evidence={"controlled": "BLOCKED"},
            rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
        )
        self.assertEqual(
            flags.delivery_for(role="generator", phase="implementation", test_project=True),
            F13_FULL,
        )

    def test_invocation_gate_denies_missing_fault_injection_or_misclassification_evidence(self) -> None:
        blocked_fault = F14FeatureFlags(
            invocation_gate_enabled=True,
            qualification_evidence={
                "controlled": "PASS",
                "fault_injection": "BLOCKED",
                "semantic_task_misclassified_as_python_only": 0,
            },
            rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
        )
        blocked_semantics = F14FeatureFlags(
            invocation_gate_enabled=True,
            qualification_evidence={
                "controlled": "PASS",
                "fault_injection": "PASS",
                "semantic_task_misclassified_as_python_only": 1,
            },
            rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
        )
        self.assertFalse(blocked_fault.invocation_gate_qualified())
        self.assertFalse(blocked_semantics.invocation_gate_qualified())

    def test_evaluator_selective_denies_without_real_parity_evidence(self) -> None:
        flags = F14FeatureFlags(
            context_delivery_mode=F14_SELECTIVE,
            selective_context_enabled=True,
            selective_roles=("evaluator",),
            selective_phases=("evaluation",),
            evaluator_selective_context=True,
            qualification_evidence={
                "controlled": "PASS",
                "evaluator_selective": "BLOCKED",
                "quality_parity": "BLOCKED",
                "real_model": "PASS",
                "real_browser": "BLOCKED",
            },
            rollout=F14RolloutPolicy(qualification_status="CONTROLLED_QUALIFIED"),
        )
        self.assertEqual(
            flags.delivery_for(role="evaluator", phase="evaluation", test_project=True),
            F13_FULL,
        )


if __name__ == "__main__":
    unittest.main()
