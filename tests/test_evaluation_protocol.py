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

from evaluation_protocol import (  # noqa: E402
    build_recheck_records,
    commit_evaluation_transaction,
    evaluate_pass_policy,
    issue_id,
    load_generator_response,
    load_issue_package,
    recover_evaluation_transaction,
    resolve_issue_route,
    validate_generator_response,
    validate_issue_package,
    validate_relative_path,
    write_generator_response_atomic,
)
from project_state import ProjectStateError  # noqa: E402


def make_issue(
    *,
    current_id="EVAL-003-001",
    category="implementation_defect",
    severity="critical",
    status="OPEN",
    route_to="GENERATOR",
):
    return {
        "issue_id": current_id,
        "category": category,
        "severity": severity,
        "title": "提交按钮不保存数据",
        "requirement_id": "REQ-ENTRY-004",
        "acceptance_criterion_id": "AC-ENTRY-004-01",
        "traceability_status": "MAPPED",
        "traceability_reason": None,
        "expected_result": "合法数据应被保存",
        "actual_result": "提交后没有新增记录",
        "reproduction_steps": ["启动应用", "提交合法表单"],
        "evidence_refs": [
            "evaluation/evidence/evaluation-003/manifest.yaml"
        ],
        "affected_scope": ["code/entry.py"],
        "allowed_scope": ["code/entry.py"],
        "forbidden_changes": [
            "memory/plans/plan-001.md",
            "config/evaluation_rules/default.yaml",
        ],
        "verification_commands": [["python", "-m", "unittest", "test_entry"]],
        "blocking": severity in {"blocker", "critical"},
        "status": status,
        "route_to": route_to,
    }


def make_package(issue=None):
    issues = [] if issue is None else [issue]
    blocking = sum(
        1
        for item in issues
        if item["blocking"] and item["status"] in {"OPEN", "REOPENED"}
    )
    route = resolve_issue_route(issues)
    result = "PASS" if route["target"] == "ACCEPTED" else (
        "BLOCKED" if route["next_status"] == "BLOCKED" else "FAIL"
    )
    return {
        "schema_version": "1.0",
        "evaluation_id": "evaluation-003",
        "project_id": "test_project",
        "result": result,
        "created_at": "2026-07-30T02:00:00+08:00",
        "current_iteration": 2,
        "return_to": route["target"],
        "report_reference": "evaluation/reports/evaluation-003.md",
        "previous_evaluation": None,
        "summary": {
            "total_issues": len(issues),
            "blocking_issues": blocking,
            "non_blocking_issues": len(issues) - blocking,
        },
        "issues": issues,
    }


def make_response(status="FIXED"):
    item = {
        "issue_id": "EVAL-003-001",
        "status": status,
    }
    if status == "FIXED":
        item.update(
            changed_files=["code/entry.py"],
            explanation="连接提交处理器",
            verification_commands=[["python", "-m", "unittest", "test_entry"]],
            verification_results=[
                {
                    "command": ["python", "-m", "unittest", "test_entry"],
                    "exit_code": 0,
                }
            ],
        )
    elif status == "PARTIALLY_FIXED":
        item.update(completed="已修复保存", remaining="尚缺错误提示")
    elif status == "NOT_FIXED":
        item["reason"] = "仍在定位"
    elif status == "NEEDS_CLARIFICATION":
        item.update(reason="重复数据行为未定义", requested_route="PLANNER")
    elif status == "CANNOT_REPRODUCE":
        item.update(
            environment={"os": "windows"},
            reproduction_steps=["运行测试"],
            observed_result="测试通过",
        )
    elif status == "OUT_OF_SCOPE":
        item["scope_reference"] = "memory/plans/plan-001.md#明确不做"
    return {
        "schema_version": "1.0",
        "source_evaluation": "evaluation-003",
        "generator_handoff_id": "handoff-004",
        "response_reference": (
            "memory/handoffs/responses/evaluation-003-response.yaml"
        ),
        "created_at": "2026-07-30T02:10:00+08:00",
        "issue_responses": [item],
    }


class IssueIdentityAndPathTests(unittest.TestCase):
    def test_stable_issue_id(self):
        self.assertEqual("EVAL-003-007", issue_id("evaluation-003", 7))

    def test_invalid_issue_sequence_is_rejected(self):
        for sequence in (0, 1000):
            with self.subTest(sequence=sequence), self.assertRaises(ProjectStateError):
                issue_id("evaluation-003", sequence)

    def test_relative_path_rejects_absolute_and_traversal(self):
        for value in (
            "C:/outside.txt",
            "/outside.txt",
            "../outside.txt",
            "code/../outside.txt",
            r"\\server\share\file.txt",
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(validate_relative_path(value))

    def test_relative_path_accepts_project_path(self):
        self.assertIsNone(validate_relative_path("code/src/entry.py"))


class IssuePackageSchemaTests(unittest.TestCase):
    def test_valid_issue_package(self):
        package = make_package(make_issue())
        self.assertEqual(
            [],
            validate_issue_package(
                package,
                filename="evaluation-003.yaml",
                markdown_reference=package["report_reference"],
            ),
        )

    def test_missing_issue_id_is_rejected(self):
        issue = make_issue()
        issue.pop("issue_id")
        self.assertTrue(any("issue_id" in item for item in validate_issue_package(make_package(issue))))

    def test_duplicate_issue_id_is_rejected(self):
        package = make_package(make_issue())
        package["issues"].append(copy.deepcopy(package["issues"][0]))
        package["summary"] = {
            "total_issues": 2,
            "blocking_issues": 2,
            "non_blocking_issues": 0,
        }
        self.assertTrue(any("重复 issue_id" in item for item in validate_issue_package(package)))

    def test_invalid_category_and_severity_are_rejected(self):
        for field, value in (("category", "unknown"), ("severity", "fatal")):
            package = make_package(make_issue())
            package["issues"][0][field] = value
            with self.subTest(field=field):
                self.assertTrue(validate_issue_package(package))

    def test_filename_must_match_evaluation(self):
        errors = validate_issue_package(
            make_package(make_issue()),
            filename="evaluation-004.yaml",
        )
        self.assertIn("文件名与 evaluation_id 不一致", errors)

    def test_markdown_reference_is_bidirectional(self):
        package = make_package(make_issue())
        errors = validate_issue_package(
            package,
            markdown_reference="evaluation/reports/evaluation-004.md",
        )
        self.assertIn("Markdown 报告与 Issue Package 互相引用不一致", errors)

    def test_unmapped_issue_requires_reason_without_fabricated_id(self):
        issue = make_issue()
        issue.update(
            requirement_id=None,
            acceptance_criterion_id=None,
            traceability_status="UNMAPPED",
            traceability_reason=None,
        )
        errors = validate_issue_package(make_package(issue))
        self.assertTrue(any("traceability_reason" in item for item in errors))

    def test_observation_does_not_force_fail(self):
        issue = make_issue(severity="observation")
        issue.update(blocking=False)
        package = make_package(issue)
        self.assertEqual("PASS", package["result"])
        self.assertEqual("ACCEPTED", package["return_to"])
        self.assertEqual([], validate_issue_package(package))

    def test_unauthorized_change_is_always_blocker(self):
        issue = make_issue(category="unauthorized_change", severity="major")
        issue["blocking"] = False
        errors = validate_issue_package(make_package(issue))
        self.assertTrue(any("未授权修改" in item for item in errors))

    def test_evidence_missing_requires_reason(self):
        package = make_package(make_issue())
        package["issues"][0]["category"] = "evidence_missing"
        errors = validate_issue_package(package)
        self.assertTrue(any("evidence_missing_reason" in item for item in errors))


class RoutingTests(unittest.TestCase):
    def test_deterministic_category_routes(self):
        cases = (
            ("implementation_defect", "GENERATOR", "IMPLEMENTING"),
            ("plan_gap", "PLANNER", "PLANNING"),
            ("requirement_ambiguity", "USER", "WAITING_FOR_USER"),
            ("environment_blocker", "SYSTEM_OR_USER", "BLOCKED"),
        )
        for category, target, status in cases:
            issue = make_issue(category=category)
            if category == "plan_gap":
                issue["route_to"] = "PLANNER"
            elif category == "requirement_ambiguity":
                issue["route_to"] = "USER"
            elif category == "environment_blocker":
                issue.update(
                    route_to="SYSTEM_OR_USER",
                    traceability_status="NOT_APPLICABLE",
                )
            with self.subTest(category=category):
                self.assertEqual(
                    {"target": target, "next_status": status},
                    resolve_issue_route([issue]),
                )

    def test_evidence_missing_routes_by_reason(self):
        cases = (
            ("generator_omission", "GENERATOR"),
            ("evaluator_environment", "SYSTEM_OR_USER"),
            ("profile_gap", "PLANNER"),
        )
        for reason, target in cases:
            issue = make_issue(category="evidence_missing")
            issue.update(evidence_missing_reason=reason, route_to=target)
            with self.subTest(reason=reason):
                self.assertEqual(target, resolve_issue_route([issue])["target"])

    def test_route_priority_selects_blocked(self):
        planner = make_issue(category="plan_gap", route_to="PLANNER")
        blocked = make_issue(
            current_id="EVAL-003-002",
            category="environment_blocker",
            severity="blocker",
            route_to="SYSTEM_OR_USER",
        )
        self.assertEqual("SYSTEM_OR_USER", resolve_issue_route([planner, blocked])["target"])


class GeneratorResponseTests(unittest.TestCase):
    def test_all_response_status_shapes(self):
        for status in (
            "FIXED",
            "PARTIALLY_FIXED",
            "NOT_FIXED",
            "CANNOT_REPRODUCE",
            "NEEDS_CLARIFICATION",
            "OUT_OF_SCOPE",
        ):
            with self.subTest(status=status):
                self.assertEqual(
                    [],
                    validate_generator_response(
                        make_response(status), make_package(make_issue())
                    ),
                )

    def test_fixed_requires_changed_files(self):
        response = make_response()
        response["issue_responses"][0].pop("changed_files")
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any("changed_files" in item for item in errors))

    def test_needs_clarification_requires_reason(self):
        response = make_response("NEEDS_CLARIFICATION")
        response["issue_responses"][0].pop("reason")
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any(".reason" in item for item in errors))

    def test_blocking_issue_cannot_be_omitted(self):
        response = make_response()
        response["issue_responses"] = []
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any("未回应" in item for item in errors))

    def test_unknown_issue_is_rejected(self):
        response = make_response()
        response["issue_responses"][0]["issue_id"] = "EVAL-003-999"
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any("未出现在" in item for item in errors))

    def test_source_evaluation_must_match(self):
        response = make_response()
        response["source_evaluation"] = "evaluation-004"
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any("source_evaluation" in item for item in errors))

    def test_verification_results_must_match_commands(self):
        response = make_response()
        response["issue_responses"][0]["verification_results"][0]["command"] = ["false"]
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any("不一一对应" in item for item in errors))

    def test_changed_file_cannot_escape_project(self):
        response = make_response()
        response["issue_responses"][0]["changed_files"] = ["../outside.py"]
        errors = validate_generator_response(response, make_package(make_issue()))
        self.assertTrue(any("changed_files" in item for item in errors))

    def test_response_safe_write_and_load_round_trip(self):
        source = make_package(make_issue())
        current = make_response()
        with tempfile.TemporaryDirectory() as directory:
            path = write_generator_response_atomic(directory, current, source)
            loaded = load_generator_response(directory, path, source)
            self.assertEqual(current, loaded)
            with self.assertRaises(ProjectStateError):
                write_generator_response_atomic(directory, current, source)


class RecheckAndPassPolicyTests(unittest.TestCase):
    def test_fixed_claim_only_resolves_after_passed_recheck(self):
        records = build_recheck_records(
            make_package(make_issue()),
            make_response(),
            {"EVAL-003-001": False},
        )
        self.assertEqual("OPEN", records[0]["current_result"])

    def test_passed_recheck_resolves_issue(self):
        records = build_recheck_records(
            make_package(make_issue()),
            make_response(),
            {"EVAL-003-001": True},
        )
        self.assertEqual("RESOLVED", records[0]["current_result"])

    def test_resolved_issue_failing_again_is_reopened(self):
        issue = make_issue(status="RESOLVED")
        issue["blocking"] = False
        package = make_package(issue)
        response = make_response()
        records = build_recheck_records(
            package, response, {"EVAL-003-001": False}
        )
        self.assertEqual("REOPENED", records[0]["current_result"])

    def test_hard_pass_policy_cannot_be_bypassed_by_score(self):
        package = make_package(make_issue(severity="blocker"))
        passed, reasons = evaluate_pass_policy(
            package,
            required_acceptance_criteria_checked=True,
            required_generator_handoff_present=True,
            required_evidence_complete=True,
            protected_artifacts_unchanged=True,
        )
        self.assertFalse(passed)
        self.assertIn("存在开放 blocker", reasons)

    def test_all_hard_conditions_are_required(self):
        package = make_package()
        values = (
            "required_acceptance_criteria_checked",
            "required_generator_handoff_present",
            "required_evidence_complete",
            "protected_artifacts_unchanged",
            "prior_blocking_issues_answered",
        )
        for field in values:
            kwargs = {item: True for item in values}
            kwargs[field] = False
            with self.subTest(field=field):
                self.assertFalse(evaluate_pass_policy(package, **kwargs)[0])

    def test_clean_package_passes_hard_policy(self):
        self.assertEqual(
            (True, []),
            evaluate_pass_policy(
                make_package(),
                required_acceptance_criteria_checked=True,
                required_generator_handoff_present=True,
                required_evidence_complete=True,
                protected_artifacts_unchanged=True,
            ),
        )


class EvaluationTransactionTests(unittest.TestCase):
    def write_state(self, path, state):
        Path(path).write_text(json.dumps(state), encoding="utf-8")

    def test_transaction_writes_artifacts_then_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.yaml").write_text("old", encoding="utf-8")
            result = commit_evaluation_transaction(
                root,
                make_package(make_issue()),
                {"status": "IMPLEMENTING"},
                state_writer=self.write_state,
            )
            self.assertTrue((root / result["issue"]).is_file())
            self.assertTrue((root / result["report"]).is_file())
            state = json.loads((root / "project.yaml").read_text(encoding="utf-8"))
            self.assertEqual(result["report"], state["last_evaluation"])
            self.assertEqual(result["issue"], state["last_issue_package"])
            journal = json.loads((root / result["journal"]).read_text(encoding="utf-8"))
            self.assertEqual("COMMITTED", journal["status"])

    def test_issue_write_failure_does_not_update_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "project.yaml"
            state.write_text("old", encoding="utf-8")
            with self.assertRaises(OSError):
                commit_evaluation_transaction(
                    root,
                    make_package(make_issue()),
                    {"status": "IMPLEMENTING"},
                    fail_at="issue_write",
                    state_writer=self.write_state,
                )
            self.assertEqual("old", state.read_text(encoding="utf-8"))
            self.assertFalse((root / "evaluation/issues/evaluation-003.yaml").exists())

    def test_markdown_failure_leaves_no_final_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.yaml").write_text("old", encoding="utf-8")
            with self.assertRaises(OSError):
                commit_evaluation_transaction(
                    root,
                    make_package(make_issue()),
                    {"status": "IMPLEMENTING"},
                    fail_at="report_write",
                    state_writer=self.write_state,
                )
            self.assertFalse((root / "evaluation/issues/evaluation-003.yaml").exists())
            self.assertFalse((root / "evaluation/reports/evaluation-003.md").exists())

    def test_state_failure_preserves_recovery_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "project.yaml"
            state.write_text("old", encoding="utf-8")
            with self.assertRaises(OSError):
                commit_evaluation_transaction(
                    root,
                    make_package(make_issue()),
                    {"status": "IMPLEMENTING"},
                    fail_at="state_write",
                    state_writer=self.write_state,
                )
            self.assertEqual("old", state.read_text(encoding="utf-8"))
            journal = json.loads(
                (
                    root
                    / "evaluation/.transactions/evaluation-003/journal.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("RECOVERY_REQUIRED", journal["status"])

    def test_recovery_required_transaction_is_idempotently_replayed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "project.yaml"
            state.write_text("status: OLD\n", encoding="utf-8")
            with self.assertRaises(OSError):
                commit_evaluation_transaction(
                    root,
                    make_package(make_issue()),
                    {"status": "IMPLEMENTING"},
                    fail_at="state_write",
                    state_writer=self.write_state,
                )
            recovered = recover_evaluation_transaction(
                root, "evaluation-003", state_writer=self.write_state
            )
            self.assertEqual("RECOVERED", recovered["result"])
            current = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(
                "evaluation/reports/evaluation-003.md",
                current["last_evaluation"],
            )
            replay = recover_evaluation_transaction(
                root, "evaluation-003", state_writer=self.write_state
            )
            self.assertEqual("IDEMPOTENT", replay["result"])

    def test_history_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "evaluation/issues/evaluation-003.yaml"
            target.parent.mkdir(parents=True)
            target.write_text("existing", encoding="utf-8")
            with self.assertRaises(ProjectStateError):
                commit_evaluation_transaction(
                    root,
                    make_package(make_issue()),
                    {"status": "IMPLEMENTING"},
                    state_writer=self.write_state,
                )

    def test_issue_package_safe_load_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = commit_evaluation_transaction(
                root,
                make_package(make_issue()),
                {"status": "IMPLEMENTING"},
                state_writer=self.write_state,
            )
            loaded = load_issue_package(root, result["issue"])
            self.assertEqual("evaluation-003", loaded["evaluation_id"])


class F91ConfigurationTests(unittest.TestCase):
    def test_json_schemas_are_valid(self):
        for name in (
            "evaluation_issue_v1.schema.json",
            "generator_response_v1.schema.json",
        ):
            data = json.loads(
                (REPO_ROOT / "config/schemas" / name).read_text(encoding="utf-8")
            )
            self.assertEqual("object", data["type"])

    def test_protocol_config_declares_required_rules(self):
        text = (REPO_ROOT / "config/evaluation_protocol.yaml").read_text(
            encoding="utf-8"
        )
        for value in (
            "unauthorized_change",
            "route_priority",
            "required_evidence_complete",
            "project_state_written_last",
        ):
            self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
