"""F10 已完成项目 Change Request 工作流测试。"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from change_request import (  # noqa: E402
    append_event,
    begin_change_implementation,
    cancel_change_request,
    classify_change,
    complete_release_rollback,
    create_change_release,
    create_change_request,
    create_impact_analysis,
    create_change_baseline,
    current_change_status,
    decide_change_request,
    load_change_request,
    migrate_completed_project_for_change_request,
    recover_change_request_state,
    record_change_evaluation,
    record_generator_change_handoff,
    request_impact_analysis_revision,
    validate_generator_change_scope,
    validate_change_request,
)
from project_state import (  # noqa: E402
    ProjectStateError,
    load_project_state,
    serialize_project_state,
    write_project_state_atomic,
)


def accepted_state(status: str = "ACCEPTED") -> dict:
    return {
        "schema_version": 6,
        "project_id": "test_completed_app",
        "project_name": "完成项目测试",
        "project_type": "application",
        "status": status,
        "current_iteration": 0,
        "iteration_sequence": 1,
        "automatic_retry_allowed": False,
        "next_role": None,
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "proposal_status": "approved",
        "user_approval_status": "approved",
        "product_spec_status": "finalized",
        "plan_status": "approved",
        "plan_approval_status": "approved",
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
        "iteration_metrics": None,
        "retry_history": [],
        "routing_disagreements": [],
        "plan_version": 1,
        "release_version": "1.0.0",
        "last_evaluation": None,
        "active_change_request": None,
        "change_cycle": 0,
        "change_context": None,
    }


def sample_request() -> dict:
    return {
        "schema_version": "1.0",
        "change_request_id": "CR-0001",
        "project_id": "test_completed_app",
        "created_at": "2026-07-30T13:22:00+08:00",
        "created_by": "user",
        "source": {
            "type": "external_feedback",
            "description": "演示后反馈",
        },
        "raw_feedback": "修复金额不更新",
        "baseline": {
            "previous_project_status": "ACCEPTED",
            "plan_version": 1,
            "release_version": "1.0.0",
            "last_evaluation": None,
            "commit_sha": None,
        },
        "requested_changes": [
            {
                "change_item_id": "CR-0001-01",
                "type": "bug_fix",
                "description": "修复金额不更新",
                "approval_status": "PENDING",
            }
        ],
    }


class ChangeRequestTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        write_project_state_atomic(self.root / "project.yaml", accepted_state())
        (self.root / "memory" / "plans").mkdir(parents=True)
        (self.root / "memory" / "plans" / "plan-001.md").write_text(
            "# 计划\n", encoding="utf-8"
        )
        (self.root / "evaluation" / "reports").mkdir(parents=True)
        self.history = self.root / "evaluation" / "reports" / "evaluation-001.md"
        self.history.write_text("# PASS\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, status: str = "ACCEPTED") -> dict:
        state = accepted_state(status)
        write_project_state_atomic(self.root / "project.yaml", state)
        return create_change_request(
            self.root,
            raw_feedback="1. 首页简化\n2. 增加 Excel 导出\n3. 修复金额不更新",
            requested_changes=[
                "首页布局简化",
                "增加 Excel 导出",
                "修复编辑金额后显示不更新",
            ],
            source_type="external_feedback",
            source_description="产品演示后收到的反馈",
        )


class ChangeRequestSchemaTests(ChangeRequestTestCase):
    def test_valid_change_request_passes(self) -> None:
        self.assertEqual([], validate_change_request(sample_request()))

    def test_missing_id_is_rejected(self) -> None:
        record = sample_request()
        record.pop("change_request_id")
        self.assertTrue(validate_change_request(record))

    def test_id_must_match_filename(self) -> None:
        errors = validate_change_request(sample_request(), filename="CR-0002.yaml")
        self.assertTrue(any("文件名" in error for error in errors))

    def test_duplicate_item_id_is_rejected(self) -> None:
        record = sample_request()
        record["requested_changes"].append(copy.deepcopy(record["requested_changes"][0]))
        self.assertTrue(any("重复" in error for error in validate_change_request(record)))

    def test_invalid_change_type_is_rejected(self) -> None:
        record = sample_request()
        record["requested_changes"][0]["type"] = "free_form"
        self.assertTrue(validate_change_request(record))

    def test_unknown_key_is_rejected(self) -> None:
        record = sample_request()
        record["silent_extension"] = True
        self.assertTrue(any("未知" in error for error in validate_change_request(record)))

    def test_project_id_mismatch_is_rejected(self) -> None:
        errors = validate_change_request(sample_request(), project_id="another")
        self.assertTrue(any("project_id" in error for error in errors))

    def test_path_traversal_is_rejected(self) -> None:
        record = sample_request()
        record["baseline"]["last_evaluation"] = "../outside.md"
        errors = validate_change_request(record, project_root=self.root)
        self.assertTrue(any("路径" in error for error in errors))

    def test_schema_files_are_valid_json(self) -> None:
        for path in (REPO_ROOT / "config" / "schemas").glob(
            "change_*_v1.schema.json"
        ):
            with self.subTest(path=path.name):
                self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)
        json.loads(
            (REPO_ROOT / "config" / "schemas" / "release_v1.schema.json").read_text(
                encoding="utf-8"
            )
        )


class CompletedProjectReopenTests(ChangeRequestTestCase):
    def test_accepted_project_can_reopen(self) -> None:
        result = self.create("ACCEPTED")
        self.assertEqual("PASS", result["result"])
        self.assertEqual(
            "CHANGE_REQUESTED", load_project_state(self.root / "project.yaml")["status"]
        )

    def test_archived_project_can_reopen(self) -> None:
        self.create("ARCHIVED")
        state = load_project_state(self.root / "project.yaml")
        self.assertEqual("ARCHIVED", state["change_context"]["previous_project_status"])

    def test_implementing_and_evaluating_cannot_reopen(self) -> None:
        for status, role in (("IMPLEMENTING", "generator"), ("EVALUATING", "evaluator")):
            state = accepted_state(status)
            state["next_role"] = role
            state["automatic_retry_allowed"] = True
            write_project_state_atomic(self.root / "project.yaml", state)
            with self.subTest(status=status), self.assertRaises(ProjectStateError):
                create_change_request(
                    self.root,
                    raw_feedback="新反馈",
                    requested_changes=["修改首页"],
                )

    def test_second_active_request_is_rejected(self) -> None:
        self.create()
        with self.assertRaises(ProjectStateError):
            create_change_request(
                self.root,
                raw_feedback="重复提交",
                requested_changes=["修改首页"],
            )

    def test_existing_request_file_is_not_overwritten(self) -> None:
        self.create()
        before = (self.root / "change_requests" / "CR-0001.yaml").read_bytes()
        with self.assertRaises(ProjectStateError):
            append_event(
                self.root,
                "CR-0001",
                "PROPOSED",
                actor="change_request",
                reason="非法重复创建",
            )
        self.assertEqual(
            before, (self.root / "change_requests" / "CR-0001.yaml").read_bytes()
        )

    def test_original_feedback_and_classification_are_preserved(self) -> None:
        self.create()
        request = load_change_request(self.root, "CR-0001")
        self.assertIn("首页简化", request["raw_feedback"])
        self.assertEqual(
            ["ui_improvement", "new_feature", "bug_fix"],
            [item["type"] for item in request["requested_changes"]],
        )

    def test_cancel_restores_original_status_and_keeps_history(self) -> None:
        self.create("ARCHIVED")
        result = cancel_change_request(
            self.root, "CR-0001", reason="用户决定暂不修改"
        )
        self.assertEqual("ARCHIVED", result["restored_project_status"])
        self.assertTrue(self.history.is_file())
        self.assertTrue((self.root / "change_requests" / "CR-0001.yaml").is_file())
        self.assertEqual("CANCELLED", current_change_status(self.root, "CR-0001"))

    def test_reopen_does_not_reset_project_history(self) -> None:
        before = self.history.read_bytes()
        self.create()
        self.assertEqual(before, self.history.read_bytes())

    def test_recovery_detects_state_drift(self) -> None:
        self.create()
        state = load_project_state(self.root / "project.yaml")
        state["status"] = "WAITING_FOR_CHANGE_APPROVAL"
        write_project_state_atomic(self.root / "project.yaml", state)
        result = recover_change_request_state(self.root)
        self.assertEqual("BLOCKED", result["result"])

    def test_classification_priority_is_deterministic(self) -> None:
        self.assertEqual("major_change", classify_change("完全改成企业级财务系统"))
        self.assertEqual("security_change", classify_change("修复认证安全问题"))
        self.assertEqual("bug_fix", classify_change("修复金额不更新"))


class PlannerImpactAndApprovalTests(ChangeRequestTestCase):
    def prepare(self) -> dict:
        self.create()
        return create_impact_analysis(self.root, "CR-0001")

    def test_planner_generates_structured_and_readable_analysis(self) -> None:
        result = self.prepare()
        self.assertTrue(Path(result["impact_analysis"]).is_file())
        self.assertTrue(Path(result["user_document"]).is_file())
        state = load_project_state(self.root / "project.yaml")
        self.assertEqual("WAITING_FOR_CHANGE_APPROVAL", state["status"])
        self.assertEqual(
            "WAITING_FOR_APPROVAL", current_change_status(self.root, "CR-0001")
        )

    def test_new_feature_gets_requirement_and_acceptance_criterion(self) -> None:
        self.prepare()
        analysis = load_change_request(self.root, "CR-0001")
        self.assertEqual("new_feature", analysis["requested_changes"][1]["type"])
        structured = (
            self.root / "change_requests" / "CR-0001" / "impact-analysis-001.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("REQ-CHANGE-02", structured)
        self.assertIn("AC-CHANGE-02-01", structured)

    def test_major_change_is_high_risk(self) -> None:
        state = accepted_state()
        write_project_state_atomic(self.root / "project.yaml", state)
        create_change_request(
            self.root,
            raw_feedback="将个人应用完全改成企业级财务系统",
            requested_changes=["将个人应用完全改成企业级财务系统"],
        )
        create_impact_analysis(self.root, "CR-0001")
        text = (
            self.root
            / "change_requests"
            / "CR-0001"
            / "impact-analysis-001.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("risk_level: HIGH", text)
        self.assertIn("requirement_action: MAJOR_REPLAN", text)

    def test_approval_requires_every_item_decision(self) -> None:
        self.prepare()
        with self.assertRaises(ProjectStateError):
            decide_change_request(
                self.root,
                "CR-0001",
                item_decisions={"CR-0001-01": "APPROVED"},
                source_text="只批准第一项",
            )

    def test_impact_revision_creates_new_version_without_overwrite(self) -> None:
        self.prepare()
        first = (
            self.root
            / "change_requests"
            / "CR-0001"
            / "impact-analysis-001.yaml"
        )
        before = first.read_bytes()
        request_impact_analysis_revision(
            self.root,
            "CR-0001",
            source_text="请补充兼容性风险后重新提交",
        )
        create_impact_analysis(self.root, "CR-0001", risk_level="HIGH")
        second = first.with_name("impact-analysis-002.yaml")
        self.assertTrue(second.is_file())
        self.assertEqual(before, first.read_bytes())
        self.assertEqual(
            second.relative_to(self.root).as_posix(),
            load_project_state(self.root / "project.yaml")["change_impact_analysis"],
        )

    def test_partial_approval_only_enters_approved_items(self) -> None:
        self.prepare()
        result = decide_change_request(
            self.root,
            "CR-0001",
            item_decisions={
                "CR-0001-01": "APPROVED",
                "CR-0001-02": "REJECTED",
                "CR-0001-03": "APPROVED",
            },
            source_text="批准首页简化和 Bug 修复，不做 Excel 导出",
        )
        self.assertEqual("PARTIALLY_APPROVED", result["decision"])
        self.assertEqual(
            ["CR-0001-01", "CR-0001-03"], result["approved_change_items"]
        )
        plan = Path(result["plan"]).read_text(encoding="utf-8")
        self.assertIn("CR-0001-01", plan)
        self.assertIn("CR-0001-03", plan)
        self.assertNotIn("### CR-0001-02", plan)

    def test_rejection_restores_state_without_code_changes(self) -> None:
        self.prepare()
        code = self.root / "code" / "app.py"
        code.parent.mkdir()
        code.write_text("print('stable')\n", encoding="utf-8")
        before = code.read_bytes()
        result = decide_change_request(
            self.root,
            "CR-0001",
            item_decisions={
                "CR-0001-01": "REJECTED",
                "CR-0001-02": "REJECTED",
                "CR-0001-03": "REJECTED",
            },
            source_text="本轮全部不做",
        )
        self.assertEqual("REJECTED", result["decision"])
        self.assertEqual(before, code.read_bytes())
        self.assertEqual(
            "ACCEPTED", load_project_state(self.root / "project.yaml")["status"]
        )

    def test_new_plan_version_preserves_old_plan(self) -> None:
        old = self.root / "memory" / "plans" / "plan-001.md"
        before = old.read_bytes()
        self.prepare()
        result = decide_change_request(
            self.root,
            "CR-0001",
            item_decisions={
                "CR-0001-01": "APPROVED",
                "CR-0001-02": "APPROVED",
                "CR-0001-03": "APPROVED",
            },
            source_text="批准全部影响分析和实施范围",
        )
        self.assertEqual(before, old.read_bytes())
        self.assertEqual("plan-002.md", Path(result["plan"]).name)
        self.assertEqual(2, result["plan_version"])


class GeneratorBaselineAndScopeTests(ChangeRequestTestCase):
    def approve(self, *, partial: bool = False) -> None:
        code = self.root / "code" / "app.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("amount = 1\n", encoding="utf-8")
        self.create()
        create_impact_analysis(self.root, "CR-0001")
        decisions = {
            "CR-0001-01": "APPROVED",
            "CR-0001-02": "REJECTED" if partial else "APPROVED",
            "CR-0001-03": "APPROVED",
        }
        decide_change_request(
            self.root,
            "CR-0001",
            item_decisions=decisions,
            source_text="批准所列实施范围",
        )

    def test_unapproved_request_cannot_start_generator(self) -> None:
        self.create()
        create_impact_analysis(self.root, "CR-0001")
        with self.assertRaises(ProjectStateError):
            begin_change_implementation(self.root, "CR-0001")

    def test_baseline_is_created_before_implementation(self) -> None:
        self.approve()
        result = begin_change_implementation(self.root, "CR-0001")
        baseline = Path(result["baseline"])
        self.assertTrue(baseline.is_file())
        self.assertIn("code/app.py", baseline.read_text(encoding="utf-8"))
        state = load_project_state(self.root / "project.yaml")
        self.assertEqual("IMPLEMENTING", state["status"])

    def test_existing_baseline_cannot_be_overwritten(self) -> None:
        self.approve()
        create_change_baseline(self.root, "CR-0001")
        before = (
            self.root
            / "change_requests"
            / "CR-0001"
            / "baseline"
            / "manifest.yaml"
        ).read_bytes()
        with self.assertRaises(ProjectStateError):
            create_change_baseline(self.root, "CR-0001")
        self.assertEqual(
            before,
            (
                self.root
                / "change_requests"
                / "CR-0001"
                / "baseline"
                / "manifest.yaml"
            ).read_bytes(),
        )

    def test_generator_cannot_implement_rejected_item(self) -> None:
        self.approve(partial=True)
        begin_change_implementation(self.root, "CR-0001")
        code = self.root / "code" / "app.py"
        code.write_text("amount = 2\n", encoding="utf-8")
        errors = validate_generator_change_scope(
            self.root,
            "CR-0001",
            implemented_items=["CR-0001-01", "CR-0001-02", "CR-0001-03"],
            changed_files=["code/app.py"],
        )
        self.assertTrue(any("未批准" in error for error in errors))

    def test_generator_cannot_modify_protected_file(self) -> None:
        self.approve()
        begin_change_implementation(self.root, "CR-0001")
        errors = validate_generator_change_scope(
            self.root,
            "CR-0001",
            implemented_items=["CR-0001-01", "CR-0001-02", "CR-0001-03"],
            changed_files=["memory/plans/plan-001.md"],
        )
        self.assertTrue(any("受保护" in error for error in errors))

    def test_handoff_must_answer_every_approved_item(self) -> None:
        self.approve()
        begin_change_implementation(self.root, "CR-0001")
        code = self.root / "code" / "app.py"
        code.write_text("amount = 2\n", encoding="utf-8")
        with self.assertRaises(ProjectStateError):
            record_generator_change_handoff(
                self.root,
                "CR-0001",
                implemented_items=[
                    {"change_item_id": "CR-0001-01", "implementation": "简化首页"}
                ],
                changed_files=["code/app.py"],
                verification_results=[{"command": "unit test", "status": "PASS"}],
                rollback="恢复稳定基线",
            )

    def test_valid_handoff_records_changed_files_and_enters_evaluation(self) -> None:
        self.approve()
        begin_change_implementation(self.root, "CR-0001")
        code = self.root / "code" / "app.py"
        code.write_text("amount = 2\n", encoding="utf-8")
        result = record_generator_change_handoff(
            self.root,
            "CR-0001",
            implemented_items=[
                {"change_item_id": item_id, "implementation": f"实现 {item_id}"}
                for item_id in ("CR-0001-01", "CR-0001-02", "CR-0001-03")
            ],
            changed_files=["code/app.py"],
            verification_results=[{"command": "unit test", "status": "PASS"}],
            rollback="恢复稳定基线或 Git commit",
        )
        self.assertEqual("EVALUATING", result["project_status"])
        handoff = Path(result["handoff"]).read_text(encoding="utf-8")
        self.assertIn("code/app.py", handoff)
        self.assertIn("changed_file_hashes", handoff)

    def test_baseline_manifest_remains_unchanged_after_code_change(self) -> None:
        self.approve()
        begin_change_implementation(self.root, "CR-0001")
        baseline = (
            self.root
            / "change_requests"
            / "CR-0001"
            / "baseline"
            / "manifest.yaml"
        )
        before = baseline.read_bytes()
        (self.root / "code" / "app.py").write_text("amount = 9\n", encoding="utf-8")
        self.assertEqual(before, baseline.read_bytes())


class EvaluatorChangeRegressionTests(ChangeRequestTestCase):
    def prepare_evaluating(self) -> None:
        code = self.root / "code" / "app.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("amount = 1\n", encoding="utf-8")
        self.create()
        create_impact_analysis(self.root, "CR-0001")
        decide_change_request(
            self.root,
            "CR-0001",
            item_decisions={
                "CR-0001-01": "APPROVED",
                "CR-0001-02": "APPROVED",
                "CR-0001-03": "APPROVED",
            },
            source_text="批准全部变更",
        )
        begin_change_implementation(self.root, "CR-0001")
        code.write_text("amount = 2\n", encoding="utf-8")
        record_generator_change_handoff(
            self.root,
            "CR-0001",
            implemented_items=[
                {"change_item_id": item_id, "implementation": f"实现 {item_id}"}
                for item_id in ("CR-0001-01", "CR-0001-02", "CR-0001-03")
            ],
            changed_files=["code/app.py"],
            verification_results=[{"command": "unit test", "status": "PASS"}],
            rollback="恢复稳定基线",
        )

    @staticmethod
    def item_results(status: str = "PASS") -> list[dict]:
        return [
            {
                "change_item_id": item_id,
                "requirement_id": f"REQ-{index:03d}",
                "acceptance_criterion_ids": [f"AC-{index:03d}-01"],
                "evidence_ids": [f"EVID-{index:03d}"],
                "status": status,
            }
            for index, item_id in enumerate(
                ("CR-0001-01", "CR-0001-02", "CR-0001-03"), 1
            )
        ]

    @staticmethod
    def regressions(status: str = "PASS") -> list[dict]:
        return [
            {
                "regression_id": "REG-001",
                "evidence_ids": ["EVID-REG-001"],
                "status": status,
            }
        ]

    def test_evaluation_must_cover_every_approved_item(self) -> None:
        self.prepare_evaluating()
        with self.assertRaises(ProjectStateError):
            record_change_evaluation(
                self.root,
                "CR-0001",
                change_item_results=self.item_results()[:2],
                regression_results=self.regressions(),
                evidence=["EVID-001"],
            )

    def test_regression_failure_cannot_pass_and_returns_generator(self) -> None:
        self.prepare_evaluating()
        result = record_change_evaluation(
            self.root,
            "CR-0001",
            change_item_results=self.item_results(),
            regression_results=self.regressions("FAIL"),
            evidence=["EVID-001", "EVID-REG-001"],
        )
        self.assertEqual("FAIL", result["result"])
        self.assertEqual("IMPLEMENTING", result["project_status"])
        self.assertEqual(1, result["evaluation_iteration"])

    def test_all_change_and_regression_checks_enter_release_ready(self) -> None:
        self.prepare_evaluating()
        result = record_change_evaluation(
            self.root,
            "CR-0001",
            change_item_results=self.item_results(),
            regression_results=self.regressions(),
            evidence=["EVID-001", "EVID-REG-001"],
        )
        self.assertEqual("PASS", result["result"])
        self.assertEqual("RELEASE_READY", result["project_status"])
        self.assertEqual(0, result["evaluation_iteration"])

    def test_environment_block_does_not_increment_iteration(self) -> None:
        self.prepare_evaluating()
        result = record_change_evaluation(
            self.root,
            "CR-0001",
            change_item_results=self.item_results(),
            regression_results=self.regressions(),
            evidence=["EVID-001"],
            environment_blocked=True,
        )
        self.assertEqual("BLOCKED", result["result"])
        self.assertEqual(0, result["evaluation_iteration"])

    def test_ambiguity_routes_planner_without_increment(self) -> None:
        self.prepare_evaluating()
        result = record_change_evaluation(
            self.root,
            "CR-0001",
            change_item_results=self.item_results(),
            regression_results=self.regressions(),
            evidence=["EVID-001"],
            ambiguity_reason="验收条件与批准范围存在冲突，需要 Planner 澄清",
        )
        self.assertEqual("PLANNER_ROUTE", result["result"])
        self.assertEqual("CHANGE_REQUESTED", result["project_status"])
        self.assertEqual(0, result["evaluation_iteration"])

    def test_fifth_failure_stops_for_user(self) -> None:
        self.prepare_evaluating()
        state = load_project_state(self.root / "project.yaml")
        state["current_iteration"] = 4
        state["change_context"]["evaluation_iteration"] = 4
        write_project_state_atomic(self.root / "project.yaml", state)
        result = record_change_evaluation(
            self.root,
            "CR-0001",
            change_item_results=self.item_results("FAIL"),
            regression_results=self.regressions(),
            evidence=["EVID-001"],
        )
        self.assertEqual("WAITING_FOR_USER", result["project_status"])
        self.assertEqual(5, result["evaluation_iteration"])
        self.assertFalse(
            load_project_state(self.root / "project.yaml")[
                "automatic_retry_allowed"
            ]
        )

    def test_handoff_hash_change_blocks_evaluation(self) -> None:
        self.prepare_evaluating()
        (self.root / "code" / "app.py").write_text("tampered = True\n", encoding="utf-8")
        with self.assertRaises(ProjectStateError):
            record_change_evaluation(
                self.root,
                "CR-0001",
                change_item_results=self.item_results(),
                regression_results=self.regressions(),
                evidence=["EVID-001"],
            )


class ReleaseAndMigrationTests(ChangeRequestTestCase):
    def complete_to_release_ready(self, change_type: str) -> None:
        code = self.root / "code" / "app.py"
        code.parent.mkdir(parents=True, exist_ok=True)
        code.write_text("version = 1\n", encoding="utf-8")
        create_change_request(
            self.root,
            raw_feedback=f"请求 {change_type}",
            requested_changes=[
                {
                    "type": change_type,
                    "description": f"实施 {change_type}",
                }
            ],
        )
        create_impact_analysis(self.root, "CR-0001")
        decide_change_request(
            self.root,
            "CR-0001",
            item_decisions={"CR-0001-01": "APPROVED"},
            source_text="批准该变更和影响分析",
        )
        begin_change_implementation(self.root, "CR-0001")
        code.write_text("version = 2\n", encoding="utf-8")
        record_generator_change_handoff(
            self.root,
            "CR-0001",
            implemented_items=[
                {"change_item_id": "CR-0001-01", "implementation": "完成变更"}
            ],
            changed_files=["code/app.py"],
            verification_results=[{"command": "unit test", "status": "PASS"}],
            rollback="恢复稳定基线",
        )
        record_change_evaluation(
            self.root,
            "CR-0001",
            change_item_results=[
                {
                    "change_item_id": "CR-0001-01",
                    "requirement_id": "REQ-CHANGE-01",
                    "acceptance_criterion_ids": ["AC-CHANGE-01-01"],
                    "evidence_ids": ["EVID-001"],
                    "status": "PASS",
                }
            ],
            regression_results=[
                {
                    "regression_id": "REG-001",
                    "evidence_ids": ["EVID-REG-001"],
                    "status": "PASS",
                }
            ],
            evidence=["EVID-001", "EVID-REG-001"],
        )

    def test_bug_fix_creates_patch_release(self) -> None:
        self.complete_to_release_ready("bug_fix")
        result = create_change_release(self.root, "CR-0001")
        self.assertEqual("1.0.1", result["release_version"])
        self.assertEqual(
            "ACCEPTED", load_project_state(self.root / "project.yaml")["status"]
        )

    def test_new_feature_creates_minor_release(self) -> None:
        self.complete_to_release_ready("new_feature")
        self.assertEqual(
            "1.1.0",
            create_change_release(self.root, "CR-0001")["release_version"],
        )

    def test_major_change_creates_major_release(self) -> None:
        self.complete_to_release_ready("major_change")
        self.assertEqual(
            "2.0.0",
            create_change_release(self.root, "CR-0001")["release_version"],
        )

    def test_old_release_is_preserved_as_explicit_migration_baseline(self) -> None:
        self.complete_to_release_ready("bug_fix")
        create_change_release(self.root, "CR-0001")
        baseline = self.root / "releases" / "release-1.0.0.yaml"
        self.assertTrue(baseline.is_file())
        text = baseline.read_text(encoding="utf-8")
        self.assertIn("status: BASELINE", text)
        self.assertIn("不表示该 Release 记录过去真实存在", text)

    def test_release_write_failure_does_not_enter_accepted(self) -> None:
        self.complete_to_release_ready("bug_fix")
        target = self.root / "releases" / "release-1.0.1.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("occupied: true\n", encoding="utf-8")
        with self.assertRaises(ProjectStateError):
            create_change_release(self.root, "CR-0001")
        self.assertEqual(
            "RELEASE_READY", load_project_state(self.root / "project.yaml")["status"]
        )

    def test_verified_rollback_keeps_new_release_and_updates_pointer(self) -> None:
        self.complete_to_release_ready("bug_fix")
        create_change_release(self.root, "CR-0001")
        new_release = self.root / "releases" / "release-1.0.1.yaml"
        result = complete_release_rollback(
            self.root,
            "1.0.0",
            reason="生产回归异常，已恢复稳定文件",
            verification_evidence=["EVID-ROLLBACK-001"],
            restoration_verified=True,
        )
        self.assertEqual("1.0.0", result["target_release"])
        self.assertTrue(new_release.is_file())
        self.assertEqual(
            "1.0.0",
            load_project_state(self.root / "project.yaml")["release_version"],
        )

    def test_rollback_without_real_evidence_is_rejected(self) -> None:
        self.complete_to_release_ready("bug_fix")
        create_change_release(self.root, "CR-0001")
        with self.assertRaises(ProjectStateError):
            complete_release_rollback(
                self.root,
                "1.0.0",
                reason="未验证",
                verification_evidence=[],
                restoration_verified=False,
            )

    def test_legacy_completed_project_is_backed_up_and_migrated(self) -> None:
        legacy = accepted_state()
        legacy["schema_version"] = 4
        for field in (
            "iteration_sequence",
            "automatic_retry_allowed",
            "iteration_metrics",
            "retry_history",
            "routing_disagreements",
            "active_change_request",
            "change_cycle",
            "change_context",
            "release_version",
        ):
            legacy.pop(field, None)
        write_project_state_atomic(self.root / "project.yaml", legacy)
        result = migrate_completed_project_for_change_request(self.root)
        self.assertTrue(result["changed"])
        self.assertTrue(Path(result["backup"]).is_file())
        self.assertTrue(Path(result["migration_record"]).is_file())
        self.assertEqual(
            6, load_project_state(self.root / "project.yaml")["schema_version"]
        )

    def test_new_change_cycle_resets_evaluation_iteration(self) -> None:
        self.complete_to_release_ready("bug_fix")
        create_change_release(self.root, "CR-0001")
        result = create_change_request(
            self.root,
            raw_feedback="第二轮修改",
            requested_changes=[{"type": "bug_fix", "description": "第二个修复"}],
        )
        state = load_project_state(self.root / "project.yaml")
        self.assertEqual("CR-0002", result["change_request_id"])
        self.assertEqual(0, state["change_context"]["evaluation_iteration"])
        self.assertEqual(2, state["change_context"]["change_cycle"])


if __name__ == "__main__":
    unittest.main()
