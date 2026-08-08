"""Stage 4 Planner WHAT/WHY 与 Generator HOW 追加式策略测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from implementation_strategy import (  # noqa: E402
    append_generator_strategy_record,
    create_planner_strategy_record,
    validate_strategy_chain,
    validate_strategy_record,
)
from project_state import ProjectStateError, parse_project_yaml  # noqa: E402


class ImplementationStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan = "memory/plans/plan-001.md"
        self.requirements = "memory/requirements/requirements_v001.yaml"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def planner_record(self) -> Path:
        return create_planner_strategy_record(
            self.root,
            source_plan=self.plan,
            source_requirements=self.requirements,
            requirement_ids=["REQ-001"],
            acceptance_criteria=["AC-001"],
            outcomes=["用户完成核心流程"],
            in_scope=["核心流程"],
            out_of_scope=["后续扩展"],
            constraints=["保持三 Agent 角色模型"],
            rationale=["先验证核心用户价值"],
        )

    def test_planner_creates_what_why_and_chain_is_valid(self) -> None:
        path = self.planner_record()
        self.assertEqual("implementation-strategy-001.yaml", path.name)
        records = validate_strategy_chain(self.root, approved_plan=self.plan)
        self.assertEqual("planner_what_why", records[0]["record_kind"])

    def test_planner_cannot_smuggle_low_level_implementation(self) -> None:
        record = {
            "schema_version": 1,
            "strategy_id": "implementation-strategy-001",
            "record_kind": "planner_what_why",
            "source_plan": self.plan,
            "source_requirements": self.requirements,
            "requirement_ids": ["REQ-001"],
            "acceptance_criteria": ["AC-001"],
            "what_why": {
                "outcomes": ["结果"],
                "in_scope": ["范围"],
                "out_of_scope": ["非目标"],
                "constraints": ["约束"],
                "rationale": ["理由"],
            },
            "classes": ["HiddenImplementation"],
        }
        with self.assertRaises(ProjectStateError):
            validate_strategy_record(record)

    def test_generator_appends_how_without_rewriting_what_why(self) -> None:
        first = self.planner_record()
        original = first.read_text(encoding="utf-8")
        second = append_generator_strategy_record(
            self.root,
            source_plan=self.plan,
            approach="沿用项目现有边界实现核心流程",
            change_set=[{"path": "code/app.py", "purpose": "实现核心流程"}],
            interfaces=["内部服务接口"],
            sequence=["先实现流程", "再运行验收测试"],
            test_commands=[["python", "-m", "pytest", "-q"]],
            rollback="回退本轮变更文件",
            risks=["外部服务不可用时需要阻塞"],
        )
        self.assertEqual("implementation-strategy-002.yaml", second.name)
        self.assertEqual(original, first.read_text(encoding="utf-8"))
        records = validate_strategy_chain(self.root, approved_plan=self.plan, require_generator=True)
        self.assertEqual("generator_how", records[1]["record_kind"])
        self.assertEqual("implementation-strategy-001", records[1]["parent_strategy_id"])

    def test_append_is_immutable_and_plan_source_is_not_replaceable(self) -> None:
        self.planner_record()
        with self.assertRaises(ProjectStateError):
            create_planner_strategy_record(
                self.root,
                source_plan=self.plan,
                source_requirements=self.requirements,
                requirement_ids=["REQ-001"],
                acceptance_criteria=["AC-001"],
                outcomes=["重复"],
                in_scope=["范围"],
                out_of_scope=["非目标"],
                constraints=["约束"],
                rationale=["理由"],
            )
        with self.assertRaises(ProjectStateError):
            append_generator_strategy_record(
                self.root,
                source_plan="memory/plans/plan-002.md",
                approach="冲突路线",
                change_set=[{"path": "code/app.py", "purpose": "冲突"}],
                interfaces=[],
                sequence=["步骤"],
                test_commands=[["python", "-m", "pytest"]],
                rollback="回退",
                risks=[],
            )

    def test_malformed_existing_record_blocks_append(self) -> None:
        directory = self.root / "memory" / "handoffs"
        directory.mkdir(parents=True)
        (directory / "implementation-strategy-001.yaml").write_text(
            "schema_version: 1\nstrategy_id: implementation-strategy-001\nrecord_kind: generator_how\nsource_plan: memory/plans/plan-001.md\n",
            encoding="utf-8",
        )
        with self.assertRaises(ProjectStateError):
            validate_strategy_chain(self.root)


if __name__ == "__main__":
    unittest.main()
