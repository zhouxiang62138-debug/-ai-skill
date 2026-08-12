"""F14-G Full Telemetry 与 Baseline Harness 回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.deterministic.benchmark import (
    BaselineBenchmarkHarness,
    BenchmarkInputs,
    default_benchmark_cases,
)
from runtime.deterministic.telemetry import RuntimeTelemetry
from runtime.session_store import SessionStore


class F14GTelemetryBaselineTests(unittest.TestCase):
    def test_telemetry_has_four_efficiency_groups_and_safe_attribution(self) -> None:
        telemetry = RuntimeTelemetry()
        telemetry.record_runtime_counter("artifact_index_reads", 2)
        telemetry.record_context_build(
            current_source_count=8,
            current_bytes=800,
            candidate_source_count=4,
            candidate_bytes=300,
            mandatory_units=2,
            mandatory_bytes=200,
            task_relevant_units=1,
            task_relevant_bytes=100,
            omitted_units=3,
            omitted_bytes=400,
            unknown_units=0,
            reused_units=3,
            rebuilt_units=1,
            reused_bytes=180,
            rebuilt_bytes=60,
        )
        telemetry.record_shadow_potential(
            formal_context_bytes=800,
            candidate_context_bytes=300,
            mandatory_complete=True,
            authority_verified=True,
            unknown_count=0,
            escalation_needed=False,
            predicted_level="L1",
        )
        telemetry.record_model_request(
            role="generator",
            phase="implementation",
            invocation_reason="implementation",
            project_revision=7,
            task_id="TASK-012",
            context_manifest_hash="a" * 64,
            actual_model_request=True,
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=20,
        )
        telemetry.record_quality(critical_false_omission=0, evaluator_independence_failures=0)
        value = telemetry.to_dict()
        self.assertEqual(2, value["contract_version"])
        self.assertEqual(2, value["runtime_efficiency"]["artifact_index_reads"])
        self.assertEqual(0.625, value["context_efficiency"]["candidate_reduction_ratio"])
        self.assertEqual(1, value["model_efficiency"]["actual_model_requests"])
        self.assertEqual(1, value["model_efficiency"]["by_role"]["generator"]["actual_model_requests"])
        self.assertEqual("actual", value["model_efficiency"]["token_status"])
        self.assertNotIn("content", json.dumps(value))

    def test_persistence_is_append_only_and_aggregates_role_cost(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_g_store_") as directory:
            root = Path(directory)
            project = root / "test_project"
            project.mkdir()
            store = SessionStore(root / "sessions.sqlite3")
            session = store.create_session("test_project", project, idempotency_key="session")
            telemetry = RuntimeTelemetry()
            telemetry.record_model_request(
                role="planner",
                phase="planning",
                invocation_reason="planning",
                project_revision=0,
                task_id="TASK-001",
                context_manifest_hash="b" * 64,
                actual_model_request=True,
            )
            self.assertTrue(
                telemetry.persist(
                    store,
                    session_id=session.session_id,
                    project_id="test_project",
                    idempotency_key="telemetry",
                )
            )
            self.assertTrue(
                telemetry.persist(
                    store,
                    session_id=session.session_id,
                    project_id="test_project",
                    idempotency_key="telemetry",
                )
            )
            self.assertEqual(1, len(store.list_telemetry(session.session_id)))
            aggregate = store.aggregate_telemetry(session.session_id)
            self.assertEqual(
                1,
                aggregate["model_efficiency"]["by_role"]["planner"]["actual_model_requests"],
            )

    def test_cases_and_controlled_baseline_are_reproducible(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_g_harness_") as directory:
            root = Path(directory)
            project = root / "test_controlled_app"
            project.mkdir()
            (project / "fixture.txt").write_text("stable fixture", encoding="utf-8")
            output = root / "benchmark-results"
            inputs = BenchmarkInputs(
                project_id="test_controlled_app",
                initial_user_request="create a timer",
                approved_requirements_hash="c" * 64,
                approved_plan_hash="d" * 64,
                project_revision=1,
                model_id="deterministic-fake-model",
                evaluation_profile_hash="e" * 64,
                test_commands=("python -m unittest",),
                browser_scenarios=("create_timer", "start_and_reset"),
                environment_policy_hash="f" * 64,
                config_hash="a" * 64,
            )
            harness = BaselineBenchmarkHarness(output, skill_root=Path.cwd())
            observed = harness.run_case(
                project_root=project,
                case_id="A",
                inputs=inputs,
                execute=lambda context: {
                    "telemetry": context.telemetry,
                    "quality": {
                        "mandatory_total": 0,
                        "mandatory_covered": 0,
                        "required_ac_total": 0,
                        "required_ac_covered": 0,
                    },
                },
            )
            repeated = harness.run_case(
                project_root=project,
                case_id="A",
                inputs=inputs,
                execute=lambda context: {
                    "telemetry": context.telemetry,
                    "quality": {
                        "mandatory_total": 0,
                        "mandatory_covered": 0,
                        "required_ac_total": 0,
                        "required_ac_covered": 0,
                    },
                },
            )
            self.assertEqual(observed.baseline.baseline_id, repeated.baseline.baseline_id)
            self.assertEqual(set("ABCDE"), set(default_benchmark_cases()))
            self.assertEqual("critical", default_benchmark_cases()["E"].dimensions["risk_level"])


if __name__ == "__main__":
    unittest.main()
