"""F9 返工、证据与重试治理测试。"""
from __future__ import annotations
import hashlib, json, sys, tempfile, unittest
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from skill_maintenance import apply_rework_retry, validate_rework_record, validate_reproducible_evidence


class ReworkGovernanceTests(unittest.TestCase):
    def record(self, kind="implementation_issue"):
        routes = {"implementation_issue": "generator", "testing_issue": "generator", "product_scope_issue": "planner", "requirement_issue": None}
        return {"schema_version": 1, "failure_class": kind, "target_role": routes.get(kind), "retry_number": 1, "source_evaluation": "evaluation/reports/evaluation-001.md", "required_evidence": "artifacts/evidence/test.json", "summary": "可复现失败", "affected_artifacts": [], "record_reference": "memory/handoffs/rework-001.md"}

    def state(self, iteration=0):
        return {"status": "EVALUATING", "current_iteration": iteration, "next_role": "evaluator", "active_module": None}

    def test_rework_routes_generator_and_counts_once(self):
        result = apply_rework_retry(self.state(), self.record())
        self.assertEqual(("IMPLEMENTING", "generator", 1), (result["status"], result["next_role"], result["current_iteration"]))

    def test_rework_routes_requirement_to_first_ask_module(self):
        result = apply_rework_retry(self.state(), self.record("requirement_issue"))
        self.assertEqual(("INTAKE", None, "first_ask_intake"), (result["status"], result["next_role"], result["active_module"]))

    def test_invalid_or_ambiguous_rework_is_rejected(self):
        bad = self.record("unknown")
        self.assertTrue(validate_rework_record(bad))
        with self.assertRaises(ValueError): apply_rework_retry(self.state(), bad)

    def test_fifth_retry_stops_for_user(self):
        record = self.record(); record["retry_number"] = 5
        result = apply_rework_retry(self.state(4), record)
        self.assertEqual(("WAITING_FOR_USER", None, 5), (result["status"], result["next_role"], result["current_iteration"]))

    def test_replay_and_wrong_retry_number_are_rejected(self):
        state = self.state(); state["rework_record"] = "memory/handoffs/rework-001.md"
        with self.assertRaises(ValueError): apply_rework_retry(state, self.record())
        record = self.record(); record["retry_number"] = 2
        with self.assertRaises(ValueError): apply_rework_retry(self.state(), record)

    def test_declared_role_must_match_failure_route(self):
        record = self.record(); record["target_role"] = "planner"
        self.assertTrue(validate_rework_record(record))

    def test_reproducible_evidence_rejects_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "target.txt"; target.write_text("actual", encoding="utf-8")
            state = {"schema_version": 5, "project_type": "skill_maintenance", "targets": {"working_repository": {"path": str(root), "access": "read_write"}, "installed_repository": {"path": str(root / "installed"), "access": "read_only_until_final_sync"}}}
            (root / "installed").mkdir()
            evidence = {"schema_version": 2, "evidence_id": "evidence-001", "captured_at": "2026-07-30T00:00:00+08:00", "command_exit_code": 0, "evidence_file": {"repository": "project", "path": "artifacts/evidence/e.json"}, "target": {"repository": "working_repository", "path": "target.txt"}, "target_sha256": "0" * 64, "command": "python -m unittest", "result": "PASS"}
            self.assertTrue(validate_reproducible_evidence(state, evidence, root))
            evidence["target_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual([], validate_reproducible_evidence(state, evidence, root))

    def test_evidence_v2_requires_reproducibility_fields(self):
        self.assertTrue(validate_reproducible_evidence({}, {}, Path(".")))

    def test_pass_evidence_rejects_nonzero_exit_code(self):
        record = {"schema_version": 2, "evidence_id": "evidence-001", "captured_at": "2026-07-30T00:00:00+08:00", "command_exit_code": 1, "result": "PASS"}
        self.assertTrue(validate_reproducible_evidence({}, record, Path(".")))

    def test_f9_schemas_templates_workflow_and_prompts_are_registered(self):
        for name in ("rework_v1.schema.json", "evidence_v2.schema.json"):
            schema = json.loads((REPO_ROOT / "config" / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual("object", schema["type"])
        workflow = (REPO_ROOT / "config" / "workflow.yaml").read_text(encoding="utf-8")
        self.assertIn("counter_increment_event: evaluator_commits_rework_route", workflow)
        self.assertIn("reject_replayed_record: true", workflow)
        self.assertIn("maximum_retries: 5", workflow)
        for path in ("prompts/generator_prompt.md", "prompts/evaluator_prompt.md", "prompts/planner_prompt.md", "templates/rework.md", "templates/evidence_v2.yaml"):
            self.assertTrue((REPO_ROOT / path).read_text(encoding="utf-8").strip())
