import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evaluation_evidence import (  # noqa: E402
    build_protected_snapshot,
    capture_environment,
    commit_reproducible_evaluation,
    compare_protected_snapshot,
    evaluate_gates,
    evidence_missing_issue,
    load_evidence_manifest,
    required_gates_passed,
    run_verified_command,
    unauthorized_change_issue,
    validate_acceptance_coverage,
    validate_delivery_handoff,
    validate_evidence_manifest,
    write_manifest_atomic,
)
from project_state import ProjectStateError  # noqa: E402


def command_record(status="PASSED", exit_code=0):
    return {
        "command_id": "CMD-001",
        "gate_id": "GATE-TESTS",
        "command": ["python", "-m", "unittest"],
        "started_at": "2026-07-30T02:00:00+08:00",
        "finished_at": "2026-07-30T02:00:01+08:00",
        "exit_code": exit_code,
        "stdout_path": (
            "evaluation/evidence/evaluation-003/commands/CMD-001.stdout.log"
        ),
        "stderr_path": (
            "evaluation/evidence/evaluation-003/commands/CMD-001.stderr.log"
        ),
        "status": status,
        "executed_by": "EVALUATOR",
        "test_metrics": {
            "passed": 1 if status == "PASSED" else 0,
            "failed": 0 if status == "PASSED" else 1,
            "skipped": 0,
            "failed_tests": [] if status == "PASSED" else ["test_failure"],
        },
    }


def make_manifest():
    return {
        "schema_version": "1.0",
        "evaluation_id": "evaluation-003",
        "created_at": "2026-07-30T02:00:00+08:00",
        "environment": {
            "os": "windows",
            "architecture": "x86_64",
            "python_version": "3.12.4",
            "working_directory": "code",
        },
        "commands": [command_record()],
        "artifacts": [
            {
                "artifact_id": "ART-001",
                "type": "test_result",
                "path": "evaluation/evidence/evaluation-003/results.json",
                "linked_issue_ids": ["EVAL-003-001"],
                "linked_requirement_ids": ["REQ-001"],
            }
        ],
        "checks": [
            {
                "check_id": "CHECK-001",
                "gate_id": "GATE-TESTS",
                "required": True,
                "requirement_id": "REQ-001",
                "acceptance_criterion_id": "AC-001-01",
                "result": "PASS",
                "evidence_refs": ["CMD-001", "ART-001"],
            }
        ],
        "gates": [
            {
                "gate_id": "GATE-TESTS",
                "required": True,
                "result": "PASS",
                "evidence_refs": ["CMD-001"],
                "skip_reason": None,
                "reason": None,
            }
        ],
    }


class EvidenceManifestTests(unittest.TestCase):
    def test_valid_manifest(self):
        self.assertEqual(
            [],
            validate_evidence_manifest(
                make_manifest(),
                filename="evaluation-003/manifest.yaml",
            ),
        )

    def test_missing_command_id_is_rejected(self):
        manifest = make_manifest()
        manifest["commands"][0].pop("command_id")
        self.assertTrue(any("command_id" in item for item in validate_evidence_manifest(manifest)))

    def test_duplicate_evidence_id_is_rejected(self):
        manifest = make_manifest()
        manifest["artifacts"].append(dict(manifest["artifacts"][0]))
        self.assertTrue(any("重复 artifact_id" in item for item in validate_evidence_manifest(manifest)))

    def test_evidence_path_traversal_is_rejected(self):
        manifest = make_manifest()
        manifest["artifacts"][0]["path"] = "../outside.log"
        self.assertTrue(any("artifacts[ART-001].path" in item for item in validate_evidence_manifest(manifest)))

    def test_unknown_evidence_reference_is_rejected(self):
        manifest = make_manifest()
        manifest["checks"][0]["evidence_refs"] = ["CMD-999"]
        self.assertTrue(any("未知 ID" in item for item in validate_evidence_manifest(manifest)))

    def test_nonzero_passed_command_is_rejected(self):
        manifest = make_manifest()
        manifest["commands"][0]["exit_code"] = 1
        self.assertTrue(any("exit_code" in item for item in validate_evidence_manifest(manifest)))

    def test_gate_order_is_enforced(self):
        manifest = make_manifest()
        manifest["gates"] = [
            {
                "gate_id": "GATE-REGRESSION",
                "required": True,
                "result": "PASS",
                "evidence_refs": ["CMD-001"],
            },
            manifest["gates"][0],
        ]
        self.assertTrue(any("固定顺序" in item for item in validate_evidence_manifest(manifest)))

    def test_required_pass_check_needs_evidence(self):
        manifest = make_manifest()
        manifest["checks"][0]["evidence_refs"] = []
        self.assertTrue(any("必需 PASS 检查" in item for item in validate_evidence_manifest(manifest)))

    def test_manifest_is_append_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = write_manifest_atomic(root, make_manifest())
            self.assertTrue(target.is_file())
            with self.assertRaises(ProjectStateError):
                write_manifest_atomic(root, make_manifest())

    def test_manifest_filename_must_match_evaluation(self):
        errors = validate_evidence_manifest(
            make_manifest(), filename="evaluation-004/manifest.yaml"
        )
        self.assertTrue(any("路径必须" in item for item in errors))

    def test_test_gate_requires_structured_counts(self):
        manifest = make_manifest()
        manifest["commands"][0].pop("test_metrics")
        self.assertTrue(
            any("test_metrics" in item for item in validate_evidence_manifest(manifest))
        )

    def test_issue_and_requirement_links_are_cross_checked(self):
        manifest = make_manifest()
        errors = validate_evidence_manifest(
            manifest,
            known_issue_ids={"EVAL-003-999"},
            known_requirement_ids={"REQ-999"},
        )
        self.assertTrue(any("linked_issue_ids" in item for item in errors))
        self.assertTrue(any("linked_requirement_ids" in item for item in errors))

    def test_manifest_safe_load_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            target = write_manifest_atomic(directory, make_manifest())
            loaded = load_evidence_manifest(directory, target)
            self.assertEqual("evaluation-003", loaded["evaluation_id"])


class GateExecutionTests(unittest.TestCase):
    def policy(self):
        return [
            {"id": "GATE-DELIVERY", "required": True},
            {"id": "GATE-BUILD", "required": False},
            {"id": "GATE-TESTS", "required": True},
            {"id": "GATE-REQUIREMENTS", "required": True},
            {"id": "GATE-REGRESSION", "required": True},
            {"id": "GATE-NON_FUNCTIONAL", "required": False},
            {"id": "GATE-EVIDENCE", "required": True},
        ]

    def passing_inputs(self):
        return {
            gate["id"]: {"result": "PASS", "evidence_refs": [f"ART-{index:03d}"]}
            for index, gate in enumerate(self.policy(), 1)
            if gate["required"]
        }

    def test_gates_execute_in_stable_order(self):
        gates = evaluate_gates(self.passing_inputs(), self.policy())
        self.assertEqual(
            [item["id"] for item in self.policy()],
            [item["gate_id"] for item in gates],
        )

    def test_required_gate_not_executed_fails(self):
        inputs = self.passing_inputs()
        inputs.pop("GATE-TESTS")
        gates = evaluate_gates(inputs, self.policy())
        tests_gate = next(item for item in gates if item["gate_id"] == "GATE-TESTS")
        self.assertEqual("FAIL", tests_gate["result"])

    def test_optional_gate_can_be_skipped_with_reason(self):
        gates = evaluate_gates(self.passing_inputs(), self.policy())
        non_functional = next(
            item for item in gates if item["gate_id"] == "GATE-NON_FUNCTIONAL"
        )
        self.assertEqual("SKIPPED", non_functional["result"])
        self.assertEqual("not_configured_for_project", non_functional["skip_reason"])

    def test_required_gate_cannot_be_skipped(self):
        inputs = self.passing_inputs()
        inputs["GATE-TESTS"] = {
            "result": "SKIPPED",
            "reason": "环境不可用",
            "blocked": True,
        }
        gates = evaluate_gates(inputs, self.policy())
        tests_gate = next(item for item in gates if item["gate_id"] == "GATE-TESTS")
        self.assertEqual("BLOCKED", tests_gate["result"])

    def test_required_gate_pass_needs_evidence(self):
        inputs = self.passing_inputs()
        inputs["GATE-EVIDENCE"]["evidence_refs"] = []
        gates = evaluate_gates(inputs, self.policy())
        evidence_gate = next(item for item in gates if item["gate_id"] == "GATE-EVIDENCE")
        self.assertEqual("FAIL", evidence_gate["result"])

    def test_all_required_gates_must_pass(self):
        gates = evaluate_gates(self.passing_inputs(), self.policy())
        self.assertEqual((True, []), required_gates_passed(gates))
        gates[0]["result"] = "FAIL"
        self.assertFalse(required_gates_passed(gates)[0])

    def test_required_acceptance_coverage_rejects_not_evaluated(self):
        complete, missing = validate_acceptance_coverage(
            ["AC-001", "AC-002"],
            [
                {"acceptance_criterion_id": "AC-001", "result": "PASS"},
                {
                    "acceptance_criterion_id": "AC-002",
                    "result": "NOT_EVALUATED",
                },
            ],
        )
        self.assertFalse(complete)
        self.assertEqual(["AC-002"], missing)

    def test_delivery_handoff_requires_response_on_rework(self):
        handoff = {
            "implementation_summary": "done",
            "changed_files": [],
            "test_results": [],
            "known_limitations": [],
            "unfinished_items": [],
            "run_instructions": ["run"],
            "handoff_id": "handoff-001",
        }
        errors = validate_delivery_handoff(
            handoff, previous_issue_package={"issues": []}
        )
        self.assertIn("返工 handoff 缺少 generator_response_reference", errors)


class SafeCommandTests(unittest.TestCase):
    METRICS = {"passed": 1, "failed": 0, "skipped": 0, "failed_tests": []}

    def test_command_result_and_logs_are_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_verified_command(
                directory,
                "evaluation-003",
                "CMD-001",
                "GATE-TESTS",
                [sys.executable, "-c", "print('ok')"],
                allowed_prefixes=[[sys.executable, "-c"]],
                test_metrics=self.METRICS,
            )
            self.assertEqual("PASSED", result["status"])
            self.assertEqual(0, result["exit_code"])
            self.assertTrue((Path(directory) / result["stdout_path"]).is_file())
            self.assertTrue((Path(directory) / result["stderr_path"]).is_file())
            self.assertEqual(1, result["test_metrics"]["passed"])

    def test_profile_logical_executable_matches_resolved_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_verified_command(
                directory,
                "evaluation-003",
                "CMD-001",
                "GATE-TESTS",
                [sys.executable, "-c", "print('ok')"],
                allowed_prefixes=[["python", "-c"]],
                test_metrics=self.METRICS,
            )
            self.assertEqual("PASSED", result["status"])

    def test_nonzero_exit_is_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_verified_command(
                directory,
                "evaluation-003",
                "CMD-001",
                "GATE-TESTS",
                [sys.executable, "-c", "raise SystemExit(7)"],
                allowed_prefixes=[[sys.executable, "-c"]],
                test_metrics={"passed": 0, "failed": 1, "skipped": 0, "failed_tests": ["test_failure"]},
            )
            self.assertEqual("FAILED", result["status"])
            self.assertEqual(7, result["exit_code"])

    def test_timeout_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_verified_command(
                directory,
                "evaluation-003",
                "CMD-001",
                "GATE-TESTS",
                [sys.executable, "-c", "import time; time.sleep(1)"],
                allowed_prefixes=[[sys.executable, "-c"]],
                timeout_seconds=0.05,
                test_metrics={"passed": 0, "failed": 0, "skipped": 0, "failed_tests": []},
            )
            self.assertEqual("TIMED_OUT", result["status"])
            self.assertIsNone(result["exit_code"])

    def test_shell_and_unlisted_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = (
                (["cmd.exe", "/c", "echo unsafe"], [["cmd.exe"]]),
                (["unknown-tool"], [["approved-tool"]]),
            )
            for command, allowed in cases:
                with self.subTest(command=command), self.assertRaises(ProjectStateError):
                    run_verified_command(
                        directory,
                        "evaluation-003",
                        "CMD-001",
                        "GATE-TESTS",
                        command,
                        allowed_prefixes=allowed,
                        test_metrics=self.METRICS,
                    )

    def test_whitelisted_but_unavailable_command_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_verified_command(
                directory,
                "evaluation-003",
                "CMD-001",
                "GATE-TESTS",
                ["definitely-not-installed-command"],
                allowed_prefixes=[["definitely-not-installed-command"]],
                test_metrics={"passed": 0, "failed": 0, "skipped": 0, "failed_tests": []},
            )
            self.assertEqual("BLOCKED", result["status"])

    def test_cwd_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProjectStateError):
                run_verified_command(
                    directory,
                    "evaluation-003",
                    "CMD-001",
                    "GATE-TESTS",
                    [sys.executable, "-c", "print('x')"],
                    allowed_prefixes=[[sys.executable, "-c"]],
                    cwd="../outside",
                    test_metrics=self.METRICS,
                )

    def test_output_is_redacted_and_truncated(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_verified_command(
                directory,
                "evaluation-003",
                "CMD-001",
                "GATE-TESTS",
                [sys.executable, "-c", "print('TOKEN=super-secret ' + 'x' * 200)"],
                allowed_prefixes=[[sys.executable, "-c"]],
                output_limit_bytes=40,
                test_metrics=self.METRICS,
            )
            text = (Path(directory) / result["stdout_path"]).read_text(encoding="utf-8")
            self.assertNotIn("super-secret", text)
            self.assertIn("OUTPUT_TRUNCATED", text)

    def test_existing_command_log_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            kwargs = dict(
                project_root=directory,
                evaluation_id="evaluation-003",
                command_id="CMD-001",
                gate_id="GATE-TESTS",
                command=[sys.executable, "-c", "print('ok')"],
                allowed_prefixes=[[sys.executable, "-c"]],
                test_metrics=self.METRICS,
            )
            run_verified_command(**kwargs)
            with self.assertRaises(ProjectStateError):
                run_verified_command(**kwargs)


class ProtectedArtifactTests(unittest.TestCase):
    def prepare(self, root):
        (root / "memory/plans").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "config/evaluation_rules").mkdir(parents=True)
        (root / "memory/plans/plan-001.md").write_text("approved", encoding="utf-8")
        (root / "tests/test_core.py").write_text("pass", encoding="utf-8")
        (root / "config/evaluation_rules/default.yaml").write_text(
            "pass_score: 8.0", encoding="utf-8"
        )

    def test_unchanged_snapshot_passes_without_git(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            snapshot = build_protected_snapshot(
                root, ["memory/plans", "tests", "config/evaluation_rules"]
            )
            self.assertTrue(compare_protected_snapshot(root, snapshot)["unchanged"])

    def test_modified_plan_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            snapshot = build_protected_snapshot(root, ["memory/plans"])
            (root / "memory/plans/plan-001.md").write_text("changed", encoding="utf-8")
            result = compare_protected_snapshot(root, snapshot)
            self.assertIn("memory/plans/plan-001.md", result["modified"])

    def test_missing_test_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            snapshot = build_protected_snapshot(root, ["tests"])
            (root / "tests/test_core.py").rename(root / "moved-test-core.py")
            result = compare_protected_snapshot(root, snapshot)
            self.assertIn("tests/test_core.py", result["missing"])

    def test_profile_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            snapshot = build_protected_snapshot(root, ["config/evaluation_rules"])
            (root / "config/evaluation_rules/default.yaml").write_text(
                "pass_score: 1.0", encoding="utf-8"
            )
            result = compare_protected_snapshot(root, snapshot)
            self.assertFalse(result["unchanged"])

    def test_added_protected_file_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            snapshot = build_protected_snapshot(root, ["tests"])
            (root / "tests/test_bypass.py").write_text("skip", encoding="utf-8")
            result = compare_protected_snapshot(root, snapshot)
            self.assertIn("tests/test_bypass.py", result["added"])

    def test_unauthorized_change_creates_blocker(self):
        issue = unauthorized_change_issue(
            "evaluation-003",
            2,
            {"missing": ["tests/test_core.py"], "added": [], "modified": []},
        )
        self.assertEqual("unauthorized_change", issue["category"])
        self.assertEqual("blocker", issue["severity"])
        self.assertTrue(issue["blocking"])

    def test_evidence_missing_routes_by_cause(self):
        cases = (
            ("generator_omission", "GENERATOR"),
            ("evaluator_environment", "SYSTEM_OR_USER"),
            ("profile_gap", "PLANNER"),
        )
        for reason, route in cases:
            with self.subTest(reason=reason):
                issue = evidence_missing_issue(
                    "evaluation-003", 3, reason=reason, title="缺少证据"
                )
                self.assertEqual(route, issue["route_to"])


class F92ConfigurationTests(unittest.TestCase):
    def test_evidence_schema_is_valid_json(self):
        schema = json.loads(
            (
                REPO_ROOT / "config/schemas/evidence_manifest_v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual("object", schema["type"])

    def test_gate_config_contains_all_required_gates_and_security(self):
        text = (REPO_ROOT / "config/evaluation_gates.yaml").read_text(
            encoding="utf-8"
        )
        for value in (
            "GATE-DELIVERY",
            "GATE-BUILD",
            "GATE-TESTS",
            "GATE-REQUIREMENTS",
            "GATE-REGRESSION",
            "GATE-NON_FUNCTIONAL",
            "GATE-EVIDENCE",
            "shell: false",
            "git_required: false",
        ):
            self.assertIn(value, text)

    def test_environment_capture_has_no_process_secrets(self):
        environment = capture_environment("code")
        self.assertEqual(
            {"os", "architecture", "python_version", "working_directory"},
            set(environment),
        )

    def test_profiles_preserve_thresholds_and_add_hard_gates(self):
        for name in ("default.yaml", "web_app.yaml"):
            text = (REPO_ROOT / "config/evaluation_rules" / name).read_text(
                encoding="utf-8"
            )
            with self.subTest(profile=name):
                self.assertIn("pass_score: 8.0", text)
                self.assertIn("blocker: 0", text)
                self.assertIn("critical: 0", text)
                self.assertIn("GATE-EVIDENCE", text)
                self.assertIn("mandatory_regression_suite", text)


class ReproducibleEvaluationTransactionTests(unittest.TestCase):
    def write_state(self, path, state):
        Path(path).write_text(json.dumps(state), encoding="utf-8")

    def issue_package(self):
        return {
            "schema_version": "1.0",
            "evaluation_id": "evaluation-003",
            "project_id": "test_project",
            "result": "PASS",
            "created_at": "2026-07-30T02:00:00+08:00",
            "current_iteration": 0,
            "return_to": "ACCEPTED",
            "report_reference": "evaluation/reports/evaluation-003.md",
            "previous_evaluation": None,
            "summary": {
                "total_issues": 0,
                "blocking_issues": 0,
                "non_blocking_issues": 0,
            },
            "issues": [],
        }

    def test_bundle_commits_manifest_before_final_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "project.yaml").write_text("old", encoding="utf-8")
            manifest = make_manifest()
            manifest["artifacts"][0]["linked_issue_ids"] = []
            result = commit_reproducible_evaluation(
                root,
                self.issue_package(),
                manifest,
                {"status": "ACCEPTED"},
                state_writer=self.write_state,
            )
            state = json.loads((root / "project.yaml").read_text(encoding="utf-8"))
            self.assertEqual(result["manifest"], state["evidence_manifest"])
            self.assertEqual(result["issue"], state["last_issue_package"])

    def test_invalid_manifest_never_updates_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "project.yaml"
            state.write_text("old", encoding="utf-8")
            invalid = make_manifest()
            invalid["commands"][0].pop("command_id")
            with self.assertRaises(ProjectStateError):
                commit_reproducible_evaluation(
                    root,
                    self.issue_package(),
                    invalid,
                    {"status": "ACCEPTED"},
                    state_writer=self.write_state,
                )
            self.assertEqual("old", state.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
