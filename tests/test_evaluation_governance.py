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

from evaluation_governance import (  # noqa: E402
    apply_controlled_retry,
    assign_stable_issue_identity,
    build_decision_summary,
    build_verification_plan,
    compute_iteration_metrics,
    decide_early_escalation,
    detect_regressions,
    issue_signature,
    select_incremental_rechecks,
    start_new_plan_iteration_sequence,
    update_repeat_history,
    update_routing_disagreements,
    write_decision_summary,
)
from evaluation_protocol import ProjectStateError, validate_issue_package  # noqa: E402


def issue(
    current_id="EVAL-001-001",
    *,
    status="OPEN",
    category="implementation_defect",
    severity="critical",
    root_cause="save-handler-missing",
):
    return {
        "issue_id": current_id,
        "category": category,
        "severity": severity,
        "title": "保存失败",
        "requirement_id": "REQ-001",
        "acceptance_criterion_id": "AC-001-01",
        "traceability_status": "MAPPED",
        "traceability_reason": None,
        "expected_result": "保存成功",
        "actual_result": "未保存",
        "reproduction_steps": ["提交表单"],
        "evidence_refs": ["evaluation/evidence/evaluation-001/manifest.yaml"],
        "affected_scope": ["code/save.py"],
        "allowed_scope": ["code/save.py"],
        "forbidden_changes": ["memory/plans/plan-001.md"],
        "verification_commands": [["python", "-m", "unittest"]],
        "blocking": severity in {"blocker", "critical"},
        "status": status,
        "route_to": "GENERATOR",
        "root_cause_key": root_cause,
        "related_test_ids": ["test_save"],
    }


def package(evaluation_id, issues, result="FAIL", return_to="GENERATOR"):
    open_issues = [
        item for item in issues if item["status"] in {"OPEN", "REOPENED"}
    ]
    blocking = sum(item["blocking"] for item in open_issues)
    return {
        "schema_version": "1.0",
        "evaluation_id": evaluation_id,
        "project_id": "test_project",
        "result": result,
        "created_at": "2026-07-30T02:00:00+08:00",
        "current_iteration": 1,
        "return_to": return_to,
        "report_reference": f"evaluation/reports/{evaluation_id}.md",
        "previous_evaluation": None,
        "summary": {
            "total_issues": len(issues),
            "blocking_issues": blocking,
            "non_blocking_issues": len(issues) - blocking,
        },
        "issues": issues,
    }


def response(status="FIXED", requested_route=None):
    item = {"issue_id": "EVAL-001-001", "status": status}
    if requested_route:
        item["requested_route"] = requested_route
    return {"issue_responses": [item]}


def evaluating_state(iteration=0):
    return {
        "project_id": "test_project",
        "status": "EVALUATING",
        "next_role": "evaluator",
        "current_iteration": iteration,
        "iteration_sequence": 1,
        "automatic_retry_allowed": True,
    }


class IncrementalRecheckTests(unittest.TestCase):
    def test_open_fixed_and_changed_file_items_are_selected(self):
        previous = package("evaluation-001", [issue()])
        selected = select_incremental_rechecks(
            previous,
            response(),
            ["code/save.py"],
            {
                "AC-001-01": ["code/save.py"],
                "AC-002-01": ["code/other.py"],
            },
        )
        self.assertEqual(["EVAL-001-001"], selected["issue_ids"])
        self.assertEqual(["AC-001-01"], selected["acceptance_criterion_ids"])
        self.assertEqual(["test_save"], selected["related_test_ids"])

    def test_changed_file_path_cannot_escape(self):
        with self.assertRaises(ProjectStateError):
            select_incremental_rechecks(
                package("evaluation-001", [issue()]),
                response(),
                ["../outside.py"],
                {},
            )

    def test_mandatory_regression_is_always_in_plan(self):
        plan = build_verification_plan(
            {"issue_ids": ["EVAL-001-001"]},
            ["build", "core_unit_tests", "build"],
        )
        self.assertEqual(["build", "core_unit_tests"], plan["mandatory_regression_suite"])
        self.assertFalse(plan["mandatory_regression_may_be_skipped"])


class StableIdentityTests(unittest.TestCase):
    def test_new_root_cause_gets_current_evaluation_id(self):
        candidate = issue()
        candidate.pop("issue_id")
        assigned = assign_stable_issue_identity(
            candidate, [], evaluation_id="evaluation-003", sequence=2
        )
        self.assertEqual("EVAL-003-002", assigned["issue_id"])
        self.assertEqual("evaluation-003", assigned["first_seen_evaluation"])

    def test_same_root_cause_reuses_old_id(self):
        candidate = issue()
        candidate.pop("issue_id")
        history = [
            {
                "issue_id": "EVAL-001-001",
                "signature": issue_signature(candidate),
                "first_seen_evaluation": "evaluation-001",
                "status": "OPEN",
            }
        ]
        assigned = assign_stable_issue_identity(
            candidate, history, evaluation_id="evaluation-003", sequence=1
        )
        self.assertEqual("EVAL-001-001", assigned["issue_id"])
        self.assertEqual("OPEN", assigned["status"])

    def test_resolved_root_cause_is_reopened(self):
        candidate = issue()
        candidate.pop("issue_id")
        history = [
            {
                "issue_id": "EVAL-001-001",
                "signature": issue_signature(candidate),
                "first_seen_evaluation": "evaluation-001",
                "status": "RESOLVED",
            }
        ]
        assigned = assign_stable_issue_identity(
            candidate, history, evaluation_id="evaluation-003", sequence=1
        )
        self.assertEqual("REOPENED", assigned["status"])

    def test_reused_id_validates_against_first_seen_evaluation(self):
        reused = issue("EVAL-001-001")
        reused["first_seen_evaluation"] = "evaluation-001"
        current = package("evaluation-003", [reused])
        current["previous_evaluation"] = "evaluation-002"
        self.assertEqual([], validate_issue_package(current))

    def test_different_root_cause_does_not_inherit_old_id(self):
        old = issue()
        candidate = issue(root_cause="database-timeout")
        candidate.pop("issue_id")
        history = [
            {
                "issue_id": old["issue_id"],
                "signature": issue_signature(old),
                "first_seen_evaluation": "evaluation-001",
                "status": "OPEN",
            }
        ]
        assigned = assign_stable_issue_identity(
            candidate, history, evaluation_id="evaluation-003", sequence=1
        )
        self.assertEqual("EVAL-003-001", assigned["issue_id"])


class IterationMetricTests(unittest.TestCase):
    def test_unknown_for_first_evaluation(self):
        metrics = compute_iteration_metrics(
            None, package("evaluation-001", [issue()])
        )
        self.assertEqual("UNKNOWN", metrics["progress_status"])

    def test_improving_when_issue_resolved(self):
        second = issue("EVAL-001-002", root_cause="second")
        previous = package("evaluation-001", [issue(), second])
        current = package("evaluation-002", [issue()])
        metrics = compute_iteration_metrics(previous, current)
        self.assertEqual("IMPROVING", metrics["progress_status"])
        self.assertEqual(1, metrics["resolved_issues"])

    def test_regressing_when_new_regression_appears(self):
        previous = package("evaluation-001", [issue()])
        regression = issue(
            "EVAL-002-001", category="regression", root_cause="regression"
        )
        current = package("evaluation-002", [issue(), regression])
        metrics = compute_iteration_metrics(previous, current)
        self.assertEqual("REGRESSING", metrics["progress_status"])
        self.assertEqual(1, metrics["new_regressions"])

    def test_stalled_when_same_issue_repeats(self):
        previous = package("evaluation-001", [issue()])
        current = package("evaluation-002", [issue()])
        metrics = compute_iteration_metrics(previous, current)
        self.assertEqual("STALLED", metrics["progress_status"])
        self.assertEqual(1, metrics["repeated_issues"])


class RepeatRegressionAndDisagreementTests(unittest.TestCase):
    def test_repeat_count_and_stalled_flag(self):
        first = update_repeat_history(
            [], package("evaluation-001", [issue()]), stalled_threshold=2
        )
        second = update_repeat_history(
            first, package("evaluation-002", [issue()]), stalled_threshold=2
        )
        self.assertEqual(2, second[0]["repeat_count"])
        self.assertTrue(second[0]["stalled_issue"])

    def test_failed_fix_claim_count_is_recorded(self):
        history = update_repeat_history(
            [],
            package("evaluation-001", [issue()]),
            stalled_threshold=3,
            failed_fix_claims={"EVAL-001-001": True},
        )
        self.assertEqual(1, history[0]["failed_fix_claim_count"])

    def test_previous_pass_now_fail_creates_regression(self):
        previous_checks = [
            {
                "evaluation_id": "evaluation-001",
                "requirement_id": "REQ-001",
                "acceptance_criterion_id": "AC-001-01",
                "result": "PASS",
            }
        ]
        current_checks = [
            {
                "requirement_id": "REQ-001",
                "acceptance_criterion_id": "AC-001-01",
                "result": "FAIL",
                "evidence_refs": ["CMD-002"],
            }
        ]
        regressions = detect_regressions(
            previous_checks,
            current_checks,
            evaluation_id="evaluation-002",
            starting_sequence=3,
            changed_files=["code/save.py"],
        )
        self.assertEqual("regression", regressions[0]["category"])
        self.assertEqual("evaluation-001", regressions[0]["previous_passed_evaluation"])
        self.assertEqual(["code/save.py"], regressions[0]["affected_scope"])

    def test_routing_disagreement_count_increments(self):
        current = update_routing_disagreements(
            [], [issue()], response("NEEDS_CLARIFICATION", "PLANNER")
        )
        current = update_routing_disagreements(
            current, [issue()], response("NEEDS_CLARIFICATION", "PLANNER")
        )
        self.assertEqual(2, current[0]["disagreement_count"])


class EarlyEscalationTests(unittest.TestCase):
    def test_routing_disagreement_escalates_to_planner(self):
        escalation = decide_early_escalation(
            [],
            [],
            [
                {
                    "issue_id": "EVAL-001-001",
                    "disagreement_count": 2,
                }
            ],
        )
        self.assertEqual("PLANNER", escalation["target"])
        self.assertEqual("PLANNING", escalation["status"])

    def test_repeated_issue_escalates_to_user(self):
        escalation = decide_early_escalation(
            [],
            [{"issue_id": "EVAL-001-001", "repeat_count": 2}],
            [],
        )
        self.assertEqual("repeated_same_issue_threshold", escalation["reason"])

    def test_repeated_failed_fix_claim_has_specific_reason(self):
        escalation = decide_early_escalation(
            [],
            [
                {
                    "issue_id": "EVAL-001-001",
                    "repeat_count": 2,
                    "failed_fix_claim_count": 2,
                }
            ],
            [],
        )
        self.assertEqual(
            "repeated_failed_fix_claim_threshold", escalation["reason"]
        )

    def test_consecutive_regressions_escalate(self):
        escalation = decide_early_escalation(
            [
                {"progress_status": "REGRESSING", "new_regressions": 1},
                {"progress_status": "REGRESSING", "new_regressions": 1},
            ],
            [],
            [],
        )
        self.assertEqual("consecutive_regression_threshold", escalation["reason"])

    def test_consecutive_no_progress_escalates(self):
        escalation = decide_early_escalation(
            [
                {"progress_status": "STABLE", "new_regressions": 0},
                {"progress_status": "STALLED", "new_regressions": 0},
            ],
            [],
            [],
        )
        self.assertEqual("no_progress_threshold", escalation["reason"])


class ControlledRetryTests(unittest.TestCase):
    def test_generator_failure_increments_once(self):
        updated = apply_controlled_retry(
            evaluating_state(1), package("evaluation-002", [issue()])
        )
        self.assertEqual(2, updated["current_iteration"])
        self.assertEqual(("IMPLEMENTING", "generator"), (updated["status"], updated["next_role"]))

    def test_pass_flow_is_accepted_without_increment(self):
        updated = apply_controlled_retry(
            evaluating_state(2),
            package("evaluation-003", [], result="PASS", return_to="ACCEPTED"),
        )
        self.assertEqual(2, updated["current_iteration"])
        self.assertEqual("ACCEPTED", updated["status"])

    def test_blocked_and_waiting_and_planner_do_not_increment(self):
        cases = (
            (
                package(
                    "evaluation-002",
                    [issue()],
                    result="BLOCKED",
                    return_to="SYSTEM_OR_USER",
                ),
                "BLOCKED",
            ),
            (
                package(
                    "evaluation-002",
                    [issue()],
                    result="FAIL",
                    return_to="USER",
                ),
                "WAITING_FOR_USER",
            ),
            (
                package(
                    "evaluation-002",
                    [issue()],
                    result="FAIL",
                    return_to="PLANNER",
                ),
                "PLANNING",
            ),
        )
        for current, expected in cases:
            with self.subTest(expected=expected):
                updated = apply_controlled_retry(evaluating_state(3), current)
                self.assertEqual(3, updated["current_iteration"])
                self.assertEqual(expected, updated["status"])

    def test_fifth_failure_stops_generator(self):
        updated = apply_controlled_retry(
            evaluating_state(4), package("evaluation-005", [issue()])
        )
        self.assertEqual(5, updated["current_iteration"])
        self.assertEqual("WAITING_FOR_USER", updated["status"])
        self.assertIsNone(updated["next_role"])
        self.assertFalse(updated["automatic_retry_allowed"])

    def test_early_escalation_stops_generator(self):
        escalation = {
            "reason": "no_progress_threshold",
            "target": "USER",
            "status": "WAITING_FOR_USER",
        }
        updated = apply_controlled_retry(
            evaluating_state(1),
            package("evaluation-002", [issue()]),
            escalation=escalation,
        )
        self.assertEqual(1, updated["current_iteration"])
        self.assertIsNone(updated["next_role"])

    def test_wrong_state_cannot_reset_or_retry(self):
        state = evaluating_state()
        state["status"] = "IMPLEMENTING"
        state["next_role"] = "generator"
        with self.assertRaises(ProjectStateError):
            apply_controlled_retry(state, package("evaluation-001", [issue()]))

    def test_new_approved_plan_starts_new_sequence(self):
        state = {
            "status": "APPROVED_FOR_IMPLEMENTATION",
            "current_iteration": 5,
            "iteration_sequence": 1,
            "approved_plan": "memory/plans/plan-001.md",
            "plan_approval_record": "memory/decisions/plan-approval-001.md",
        }
        updated = start_new_plan_iteration_sequence(
            state,
            new_approved_plan="memory/plans/plan-002.md",
            new_plan_approval_record="memory/decisions/plan-approval-002.md",
        )
        self.assertEqual(0, updated["current_iteration"])
        self.assertEqual(2, updated["iteration_sequence"])

    def test_same_plan_cannot_reset_iteration(self):
        state = {
            "status": "APPROVED_FOR_IMPLEMENTATION",
            "current_iteration": 5,
            "approved_plan": "memory/plans/plan-001.md",
            "plan_approval_record": "memory/decisions/plan-approval-001.md",
        }
        with self.assertRaises(ProjectStateError):
            start_new_plan_iteration_sequence(
                state,
                new_approved_plan="memory/plans/plan-001.md",
                new_plan_approval_record="memory/decisions/plan-approval-001.md",
            )

    def test_complete_generator_evaluator_generator_then_pass_loop(self):
        state = evaluating_state(0)
        state = apply_controlled_retry(
            state, package("evaluation-001", [issue()])
        )
        self.assertEqual("generator", state["next_role"])
        state.update(status="EVALUATING", next_role="evaluator")
        state = apply_controlled_retry(
            state,
            package("evaluation-002", [], result="PASS", return_to="ACCEPTED"),
        )
        self.assertEqual("ACCEPTED", state["status"])
        self.assertEqual(1, state["current_iteration"])


class DecisionSummaryTests(unittest.TestCase):
    def test_summary_contains_complete_history(self):
        current = issue()
        metrics = [{"progress_status": "STALLED", "new_regressions": 0}]
        repeats = [{"issue_id": current["issue_id"], "repeat_count": 2}]
        summary = build_decision_summary(
            evaluating_state(5),
            [package("evaluation-001", [current])],
            metrics,
            repeats,
            [response()],
            [{"issue_id": current["issue_id"], "current_result": "OPEN"}],
            reason="maximum_iterations_reached",
        )
        for field in (
            "remaining_issues",
            "repeated_issues",
            "resolved_issue_ids",
            "new_regressions",
            "trend_history",
            "generator_fix_history",
            "evaluator_recheck_history",
            "failure_causes",
            "options",
        ):
            self.assertIn(field, summary)

    def test_decision_summaries_are_append_only(self):
        summary = build_decision_summary(
            evaluating_state(5), [], [], [], [], [], reason="limit"
        )
        with tempfile.TemporaryDirectory() as directory:
            first = write_decision_summary(directory, summary)
            second = write_decision_summary(directory, summary)
            self.assertNotEqual(first, second)
            self.assertEqual("decision-summary-001.yaml", first.name)
            self.assertEqual("decision-summary-002.yaml", second.name)


class F93ConfigurationTests(unittest.TestCase):
    def test_governance_schemas_are_valid_json(self):
        for name in (
            "iteration_metrics_v1.schema.json",
            "decision_summary_v1.schema.json",
        ):
            data = json.loads(
                (REPO_ROOT / "config/schemas" / name).read_text(encoding="utf-8")
            )
            self.assertEqual("object", data["type"])

    def test_config_declares_thresholds_and_maximum(self):
        text = (REPO_ROOT / "config/retry_governance.yaml").read_text(
            encoding="utf-8"
        )
        for value in (
            "repeated_same_issue: 2",
            "repeated_failed_fix_claim: 2",
            "consecutive_regression_rounds: 2",
            "routing_disagreement_rounds: 2",
            "no_progress_rounds: 2",
            "maximum_automatic_iterations: 5",
            "preserve_history_across_reset: true",
        ):
            self.assertIn(value, text)


if __name__ == "__main__":
    unittest.main()
