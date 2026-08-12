"""Stage 5 条件式 Implementation Contract 测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from implementation_contract import (  # noqa: E402
    classify_contract_need,
    create_implementation_contract,
    validate_contract,
    validate_contract_history,
)
from project_state import ProjectStateError  # noqa: E402


class ConditionalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = "memory/plans/plan-001.md"
        self.requirements = {"REQ-001"}
        self.criteria = {"AC-001", "AC-002"}

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def feature(self, **updates: object) -> dict:
        value: dict[str, object] = {
            "feature_id": "feature-entry",
            "risk_tags": [],
            "irreversible": False,
            "critical_workflow_pages": 0,
            "acceptance_criteria_count": 2,
        }
        value.update(updates)
        return value

    def create(
        self,
        *,
        verification: list[str] | None = None,
        **updates: object,
    ) -> Path | None:
        return create_implementation_contract(
            self.root,
            feature=self.feature(**updates),
            source_plan=self.plan,
            requirements=["REQ-001"],
            acceptance_criteria=["AC-001"],
            done_when=["保存后刷新仍可读取"],
            verification=verification or ["unit", "integration", "persistence", "regression"],
            rollback_expectation="恢复上一个快照并回退迁移",
            risks=["数据结构变化"],
            approved_plan=self.plan,
            approved_requirements=self.requirements,
            approved_acceptance_criteria=self.criteria,
        )

    def test_ordinary_task_stays_on_normal_path(self) -> None:
        decision = classify_contract_need(self.feature())
        self.assertFalse(decision.required)
        self.assertEqual((), decision.triggers)
        self.assertIsNone(self.create())
        self.assertFalse((self.root / "memory" / "handoffs").exists())

    def test_high_risk_tags_trigger_contract(self) -> None:
        for tag in (
            "database_migration",
            "authentication",
            "authorization",
            "payment",
            "destructive_operation",
            "external_api_integration",
            "complex_state_machine",
            "data_compatibility",
            "high_risk_change_request",
        ):
            with self.subTest(tag=tag):
                decision = classify_contract_need(self.feature(risk_tags=[tag]))
                self.assertTrue(decision.required)
                self.assertIn(tag, decision.triggers)

    def test_threshold_and_irreversible_triggers_are_deterministic(self) -> None:
        self.assertTrue(
            classify_contract_need(self.feature(irreversible=True)).required
        )
        decision = classify_contract_need(
            self.feature(critical_workflow_pages=2, acceptance_criteria_count=5)
        )
        self.assertEqual(
            ("critical_workflow_pages", "many_acceptance_criteria"), decision.triggers
        )

    def test_contract_requires_risk_specific_verification_and_is_append_only(self) -> None:
        path = self.create(risk_tags=["external_api_integration"])
        assert path is not None
        self.assertEqual("implementation-contract-001.yaml", path.name)
        records = validate_contract_history(
            self.root,
            approved_plan=self.plan,
            approved_requirements=self.requirements,
            approved_acceptance_criteria=self.criteria,
        )
        self.assertEqual("external_api_integration", records[0]["risk_triggers"][0])
        with self.assertRaises(ProjectStateError):
            self.create(
                risk_tags=["external_api_integration"], verification=["unit"]
            )

    def test_contract_cannot_expand_approved_sources_or_become_approval_gate(self) -> None:
        with self.assertRaises(ProjectStateError):
            create_implementation_contract(
                self.root,
                feature=self.feature(risk_tags=["payment"]),
                source_plan=self.plan,
                requirements=["REQ-NEW"],
                acceptance_criteria=["AC-001"],
                done_when=["完成"],
                verification=["integration", "regression"],
                rollback_expectation="回滚",
                risks=[],
                approved_plan=self.plan,
                approved_requirements=self.requirements,
                approved_acceptance_criteria=self.criteria,
            )
        contract = {
            "schema_version": 1,
            "contract_id": "implementation-contract-001",
            "feature_id": "feature-entry",
            "source_plan": self.plan,
            "requirements": ["REQ-001"],
            "acceptance_criteria": ["AC-001"],
            "risk_triggers": ["payment"],
            "done_when": ["完成"],
            "verification": ["integration", "regression"],
            "rollback_expectation": "回滚",
            "risks": [],
            "next_role": "user",
        }
        with self.assertRaises(ProjectStateError):
            validate_contract(contract)


if __name__ == "__main__":
    unittest.main()
