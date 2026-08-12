"""F14-E Incremental Context 的无损复用、失效、回源和恢复测试。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from runtime.context import (
    CanonicalSummary,
    CanonicalSummaryCache,
    DeterministicSummaryBuilder,
    IncrementalContextBuilder,
    IncrementalContextManifest,
    SemanticSummaryShadowBuilder,
    SummarySource,
    assert_summary_usable,
    compare_mandatory_context,
    validate_summary_sources,
)
from runtime.deterministic.source_cache import SourceCache
from runtime.deterministic.store import DerivedRuntimeStore
from runtime.deterministic.diff_index import DiffIndexBuilder
from runtime.deterministic.test_parser import TestOutputParser
from runtime.deterministic.telemetry import RuntimeTelemetry
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from tests.runtime_test_support import make_store


SOURCE_HASH = "a" * 64
POLICY_HASH = "b" * 64


def _unit(
    unit_id: str,
    *,
    source_hash: str = SOURCE_HASH,
    revision: int = 1,
    policy_hash: str = POLICY_HASH,
    role: str = "generator",
    dependencies: tuple[str, ...] = (),
    authority: str = "UNKNOWN",
    kind: str = "source",
    parser_version: str = "parser-v1",
    summary_version: str = "none",
    delivery: str = "REFERENCE",
    cacheable: bool = True,
):
    return IncrementalContextBuilder.unit_from_source(
        unit_id=unit_id,
        source_ref=unit_id,
        exact_locator=unit_id,
        source_hash=source_hash,
        authority=authority,
        role=role,
        project_revision=revision,
        policy_hash=policy_hash,
        parser_version=parser_version,
        summary_version=summary_version,
        dependency_ids=dependencies,
        delivery=delivery,
        cacheable=cacheable,
        kind=kind,
    )


class F14EIncrementalContextTests(unittest.TestCase):
    def test_unchanged_sources_are_reused_exactly(self) -> None:
        builder = IncrementalContextBuilder()
        first, first_delta = builder.build(
            previous=None,
            current_units=[_unit("code/app.py"), _unit("test_app")],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        second, delta = builder.build(
            previous=first,
            current_units=[_unit("code/app.py"), _unit("test_app")],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        self.assertEqual(["code/app.py", "test_app"], list(first_delta.added_units))
        self.assertEqual(["code/app.py", "test_app"], list(delta.reused_units))
        self.assertEqual([], list(delta.invalidated_units))
        self.assertEqual(first.manifest_hash, delta.base_manifest_hash)
        self.assertTrue(compare_mandatory_context(first, second)["equivalent"])

    def test_source_mutation_rebuilds_only_changed_unit(self) -> None:
        builder = IncrementalContextBuilder()
        previous, _ = builder.build(
            previous=None,
            current_units=[_unit("code/app.py"), _unit("code/other.py")],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        mutated_units = [
            _unit("code/app.py", source_hash="c" * 64),
            _unit("code/other.py"),
        ]
        current, delta = builder.build(
            previous=previous,
            current_units=mutated_units,
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        self.assertEqual(("code/app.py",), delta.changed_units)
        self.assertEqual(("code/other.py",), delta.reused_units)
        self.assertEqual("REBUILT", current.units[0].reuse_status)
        self.assertEqual("SOURCE_HASH_CHANGED", current.units[0].invalidation_reason)
        full, _ = builder.build(
            previous=None,
            current_units=mutated_units,
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        equivalence = compare_mandatory_context(full, current)
        self.assertEqual([], equivalence["mandatory_difference"])
        self.assertEqual([], equivalence["authority_difference"])
        self.assertEqual([], equivalence["coverage_difference"])
        self.assertTrue(equivalence["equivalent"])

    def test_explicit_dependency_graph_propagates_invalidation(self) -> None:
        builder = IncrementalContextBuilder()
        previous, _ = builder.build(
            previous=None,
            current_units=[
                _unit("AC-007"),
                _unit("TASK-012", dependencies=("AC-007",)),
                _unit("code/storage.py", dependencies=("TASK-012",)),
                _unit("test_storage", dependencies=("code/storage.py",)),
            ],
            role="generator",
            task_identity="TASK-012",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        _, delta = builder.build(
            previous=previous,
            current_units=[
                _unit("AC-007"),
                _unit("TASK-012", dependencies=("AC-007",)),
                _unit("code/storage.py", dependencies=("TASK-012",)),
                _unit("test_storage", dependencies=("code/storage.py",)),
            ],
            role="generator",
            task_identity="TASK-012",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
            changed_node_ids=("REQ-004",),
            explicit_edges=(
                {"source": "REQ-004", "target": "AC-007", "confidence": "explicit"},
                {"source": "AC-007", "target": "TASK-012", "confidence": "explicit"},
                {"source": "TASK-012", "target": "code/storage.py", "confidence": "explicit"},
                {"source": "code/storage.py", "target": "test_storage", "confidence": "explicit"},
            ),
        )
        self.assertEqual(
            ("AC-007", "TASK-012", "code/storage.py", "test_storage"),
            delta.invalidated_units,
        )

    def test_unknown_dependency_requires_fallback(self) -> None:
        builder = IncrementalContextBuilder()
        previous, _ = builder.build(
            previous=None,
            current_units=[_unit("code/app.py", dependencies=("UNKNOWN-1",))],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        _, delta = builder.build(
            previous=previous,
            current_units=[_unit("code/app.py", dependencies=("UNKNOWN-1",))],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
            unknown_node_ids=("UNKNOWN-1",),
        )
        self.assertEqual(("code/app.py",), delta.unknown_units)
        self.assertTrue(delta.fallback_required)

    def test_diff_index_drives_partial_invalidation_without_name_inference(self) -> None:
        diff = DiffIndexBuilder.build(
            baseline_revision=1,
            current_revision=2,
            baseline_files={"code/app.py": "a", "code/other.py": "b"},
            current_files={"code/app.py": "changed", "code/other.py": "b"},
            explicitly_affected_nodes=("TASK-001",),
        )
        builder = IncrementalContextBuilder()
        previous, _ = builder.build(
            previous=None,
            current_units=[
                _unit("code/app.py"),
                _unit("code/other.py"),
                _unit("TASK-001", kind="plan_task"),
            ],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        _, delta = builder.build_from_diff_index(
            diff_index=diff,
            previous=previous,
            current_units=[
                _unit("code/app.py", revision=2, source_hash="c" * 64),
                _unit("code/other.py", revision=2),
                _unit("TASK-001", kind="plan_task", revision=2),
            ],
            role="generator",
            task_identity="TASK-001",
            project_revision=2,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        self.assertIn("code/app.py", delta.invalidated_units)
        self.assertIn("TASK-001", delta.invalidated_units)
        self.assertIn("code/other.py", delta.changed_units)

    def test_global_constraint_revision_policy_parser_and_delivery_invalidate(self) -> None:
        builder = IncrementalContextBuilder()
        previous, _ = builder.build(
            previous=None,
            current_units=[
                _unit("SECURITY-1", authority="SECURITY_CONSTRAINT", kind="constraint"),
                _unit("code/app.py"),
            ],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        current, delta = builder.build(
            previous=previous,
            current_units=[
                _unit("SECURITY-1", authority="SECURITY_CONSTRAINT", kind="constraint", revision=2),
                _unit("code/app.py", revision=2),
            ],
            role="generator",
            task_identity="TASK-001",
            project_revision=2,
            policy_hash=POLICY_HASH,
            parser_version="parser-v2",
            summary_version="summary-v2",
            changed_global_constraints=("SECURITY-1",),
        )
        self.assertIn("SECURITY-1", delta.invalidated_units)
        self.assertIn("code/app.py", delta.changed_units)
        self.assertTrue(all(unit.project_revision == 2 for unit in current.units))

    def test_policy_change_and_coverage_failure_require_safe_rebuild(self) -> None:
        builder = IncrementalContextBuilder()
        previous, _ = builder.build(
            previous=None,
            current_units=[_unit("code/app.py")],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        _, delta = builder.build(
            previous=previous,
            current_units=[_unit("code/app.py", policy_hash="d" * 64)],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash="d" * 64,
            parser_version="parser-v1",
            summary_version="none",
            coverage_result="UNKNOWN",
        )
        self.assertEqual(("code/app.py",), delta.changed_units)
        self.assertTrue(delta.fallback_required)

    def test_manifest_and_delta_round_trip_and_partial_write_is_untrusted(self) -> None:
        builder = IncrementalContextBuilder()
        manifest, delta = builder.build(
            previous=None,
            current_units=[_unit("code/app.py")],
            role="generator",
            task_identity="TASK-001",
            project_revision=1,
            policy_hash=POLICY_HASH,
            parser_version="parser-v1",
            summary_version="none",
        )
        with tempfile.TemporaryDirectory(prefix="test_f14_e_store_") as directory:
            store, session_id = make_store(directory)
            derived = DerivedRuntimeStore(store)
            manifest_id = derived.write_incremental_manifest(
                session_id=session_id,
                project_id="test_runtime",
                manifest=manifest,
                status="PARTIAL",
            )
            with self.assertRaisesRegex(RuntimeStorageError, "UNTRUSTED"):
                derived.read_incremental_manifest(session_id, manifest_id)
            complete_id = derived.write_incremental_manifest(
                session_id=session_id,
                project_id="test_runtime",
                manifest=manifest,
            )
            delta_id = derived.write_context_delta(
                session_id=session_id,
                project_id="test_runtime",
                delta=delta,
            )
            self.assertEqual(manifest.manifest_hash, derived.read_incremental_manifest(session_id, complete_id)["manifest"]["manifest_hash"])
            self.assertEqual(delta.to_dict(), derived.read_context_delta(session_id, delta_id)["delta"])

    def test_deterministic_summary_is_revalidatable_and_semantic_is_shadow_only(self) -> None:
        source = SummarySource("test-output", "evidence/test-output.json", "e" * 64)
        deterministic = DeterministicSummaryBuilder().build(
            summary_id="summary-1",
            source_refs=(source,),
            project_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="evaluator",
            generator_version="summary-v1",
            content={"passed": 8, "failed": 0},
            coverage={"tests": True},
        )
        valid = validate_summary_sources(
            deterministic,
            current_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="evaluator",
            source_hashes={source.exact_locator: source.source_hash},
            available_locators=(source.exact_locator,),
        )
        self.assertTrue(valid.valid)
        invalid = validate_summary_sources(
            deterministic,
            current_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="evaluator",
            source_hashes={},
            available_locators=(),
        )
        self.assertFalse(invalid.valid)
        with self.assertRaisesRegex(RuntimeStorageError, "SUMMARY_INVALID"):
            assert_summary_usable(invalid)

        semantic = SemanticSummaryShadowBuilder().build(
            summary_id="semantic-1",
            source_refs=(source,),
            project_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="evaluator",
            generator_version="model-v1",
            model_id="model-test",
            content={"conclusion": "shadow"},
        )
        with self.assertRaisesRegex(RuntimeValidationError, "SEMANTIC_SUMMARY_SHADOW_ONLY"):
            assert_summary_usable(semantic)
        with self.assertRaisesRegex(RuntimeValidationError, "EVALUATOR_SEMANTIC_SUMMARY_FORBIDDEN"):
            assert_summary_usable(semantic, evaluator=True)
        self.assertEqual(semantic.summary_hash, CanonicalSummary.from_mapping(semantic.to_dict()).summary_hash)

    def test_browser_summary_is_deterministic_and_preserves_raw_locator(self) -> None:
        source = SummarySource("browser-run", "evidence/browser.json", "e" * 64)
        summary = DeterministicSummaryBuilder().build_browser_evidence(
            summary_id="browser-summary",
            source_refs=(source,),
            project_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="evaluator",
            generator_version="browser-summary-v1",
            bundle={
                "browser_runs": [{"result": "PASS"}],
                "browser_evidence": [{"result": "PASS"}, {"result": "PASS"}],
            },
        )
        self.assertEqual(1, summary.content["browser_run_count"])
        self.assertTrue(summary.coverage["raw_evidence_locator_preserved"])
        self.assertEqual(source.exact_locator, summary.source_refs[0].exact_locator)

    def test_summary_cache_rejects_cross_role_and_evaluator_semantic_reuse(self) -> None:
        source = SummarySource("evidence", "evidence/result.json", "e" * 64)
        deterministic = DeterministicSummaryBuilder().build(
            summary_id="cache-summary",
            source_refs=(source,),
            project_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="generator",
            generator_version="summary-v1",
            content={"passed": 1},
        )
        semantic = SemanticSummaryShadowBuilder().build(
            summary_id="cache-semantic",
            source_refs=(source,),
            project_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="generator",
            generator_version="model-v1",
            model_id="model-test",
            content={"conclusion": "shadow"},
        )
        with tempfile.TemporaryDirectory(prefix="test_f14_e_cache_") as directory:
            store, session_id = make_store(directory)
            cache = CanonicalSummaryCache(store)
            cache.put(session_id=session_id, project_id="test_runtime", summary=deterministic)
            cache.put(session_id=session_id, project_id="test_runtime", summary=semantic)
            with self.assertRaisesRegex(RuntimeStorageError, "SUMMARY_INVALID"):
                cache.get(
                    session_id=session_id,
                    summary_id=deterministic.summary_id,
                    current_revision=1,
                    policy_hash=POLICY_HASH,
                    role_scope="evaluator",
                    source_hashes={source.exact_locator: source.source_hash},
                    available_locators=(source.exact_locator,),
                )
            with self.assertRaisesRegex(RuntimeValidationError, "EVALUATOR_SEMANTIC_SUMMARY_FORBIDDEN"):
                cache.get(
                    session_id=session_id,
                    summary_id=semantic.summary_id,
                    current_revision=1,
                    policy_hash=POLICY_HASH,
                    role_scope="generator",
                    source_hashes={source.exact_locator: source.source_hash},
                    available_locators=(source.exact_locator,),
                    evaluator=True,
                )

    def test_summary_storage_corruption_is_detected(self) -> None:
        source = SummarySource("test-output", "evidence/test-output.json", "e" * 64)
        summary = DeterministicSummaryBuilder().build(
            summary_id="summary-store",
            source_refs=(source,),
            project_revision=1,
            policy_hash=POLICY_HASH,
            role_scope="generator",
            generator_version="summary-v1",
            content={"passed": 1},
        )
        with tempfile.TemporaryDirectory(prefix="test_f14_e_summary_") as directory:
            store, session_id = make_store(directory)
            derived = DerivedRuntimeStore(store)
            derived.write_canonical_summary(
                session_id=session_id,
                project_id="test_runtime",
                summary=summary,
            )
            connection = store.raw_connection()
            try:
                connection.execute("DROP TRIGGER f14_canonical_summaries_no_update")
                connection.execute(
                    "UPDATE f14_canonical_summaries SET summary_json=? WHERE summary_id=?",
                    ("{\"corrupt\":true}", summary.summary_id),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeStorageError, "SUMMARY_CORRUPT"):
                derived.read_canonical_summary(session_id, summary.summary_id)

    def test_test_parser_reuses_validated_result_without_parser_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_e_parser_") as directory:
            root = Path(directory)
            output = root / "pytest.txt"
            output.write_text("2 passed\n", encoding="utf-8")
            store, _ = make_store(directory)
            telemetry = RuntimeTelemetry()
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash=POLICY_HASH,
                role_scope="evaluator",
                parser_version="f14-test-parser-v1",
                store=store,
                telemetry=telemetry,
            )
            parser = TestOutputParser(source_cache=cache, telemetry=telemetry)
            first = parser.parse(
                output,
                command="pytest",
                exit_code=0,
                duration_ms=None,
                raw_log_locator="logs/pytest.txt",
            )
            second = parser.parse(
                output,
                command="pytest",
                exit_code=0,
                duration_ms=None,
                raw_log_locator="logs/pytest.txt",
            )
            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(1, telemetry.to_dict()["runtime_efficiency"]["parser_runs"])
            self.assertEqual(1, telemetry.to_dict()["runtime_efficiency"]["file_reads"])


if __name__ == "__main__":
    unittest.main()


def test_context_builder_incremental_resume_keeps_formal_f13_and_reuses_units(tmp_path) -> None:
    """Builder 集成测试：增量旁路不能改变正式 F13 Package。"""

    from tests.test_formal_context_builder import _prepare_project
    from runtime.context import ContextBuildRequest, ContextBuilder

    store, session_id, run_id, _ = _prepare_project(tmp_path, "IMPLEMENTING", "generator")
    request = ContextBuildRequest(
        session_id,
        run_id,
        "generator",
        task_identity="TASK-012",
    )
    builder = ContextBuilder(store)
    first_formal = builder.build(request)
    first_manifest, _ = builder.build_incremental_manifest(request, current=first_formal)
    second_formal, second_manifest, delta = builder.build_incremental_resume(
        request,
        previous=first_manifest,
    )
    assert first_formal.context_hash == second_formal.context_hash
    assert first_formal.context_type == "ROLE_SCOPED"
    assert delta.reused_units
    assert second_manifest.coverage_result == "VALID"
