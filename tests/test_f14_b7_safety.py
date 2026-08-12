import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.deterministic.artifact_index import ArtifactIndexBuilder
from runtime.deterministic.dependency_graph import DependencyEdge, DependencyGraph
from runtime.deterministic.source_cache import SourceCache
from runtime.deterministic.store import DerivedRuntimeStore
from runtime.deterministic.telemetry import RuntimeTelemetry
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from tests.runtime_test_support import make_store


HASH = "b" * 64


class F14B7SafetyTests(unittest.TestCase):
    def test_artifact_mutation_creates_new_hash_and_does_not_change_business_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_b7_mutation_") as directory:
            root = Path(directory)
            project_state = root / "project.yaml"
            project_state.write_text("status: PLANNING\n", encoding="utf-8")
            source = root / "approved.yaml"
            source.write_text("value: one\n", encoding="utf-8")
            original_project = project_state.read_text(encoding="utf-8")
            store, session_id = make_store(directory)
            builder = ArtifactIndexBuilder(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                producer_role="planner",
            )
            first = builder.build(
                [
                    {
                        "artifact_id": "approved",
                        "kind": "approved",
                        "locator": "approved.yaml",
                        "authority": "GENERATED_ARTIFACT",
                        "approval_status": "UNKNOWN",
                    }
                ]
            )
            source.write_text("value: two\n", encoding="utf-8")
            second = builder.build(
                [
                    {
                        "artifact_id": "approved",
                        "kind": "approved",
                        "locator": "approved.yaml",
                        "authority": "GENERATED_ARTIFACT",
                        "approval_status": "UNKNOWN",
                    }
                ]
            )
            self.assertNotEqual(first[0].content_hash, second[0].content_hash)
            builder.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
                records=second,
            )
            self.assertEqual(original_project, project_state.read_text(encoding="utf-8"))

    def test_revision_and_parser_changes_invalidate_source_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_b7_invalidation_") as directory:
            root = Path(directory)
            (root / "source.txt").write_text("same", encoding="utf-8")
            store, _session_id = make_store(directory)
            first_telemetry = RuntimeTelemetry()
            first = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
                store=store,
                telemetry=first_telemetry,
            )
            first.read("source.txt")
            second_telemetry = RuntimeTelemetry()
            second = SourceCache(
                root,
                project_revision=2,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v2",
                store=store,
                telemetry=second_telemetry,
            )
            result = second.read("source.txt", require_trusted_hash=False)
            self.assertTrue(result.trusted)
            self.assertEqual(1, second_telemetry.to_dict()["runtime_efficiency"]["file_reads"])

    def test_cache_write_failure_does_not_break_source_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_b7_cache_write_") as directory:
            root = Path(directory)
            (root / "source.txt").write_text("readable", encoding="utf-8")
            store, _session_id = make_store(directory)
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
                store=store,
            )
            with patch(
                "runtime.deterministic.source_cache.DerivedRuntimeStore.put_content_cache",
                side_effect=OSError("cache write crash"),
            ):
                result = cache.read("source.txt")
            self.assertEqual(b"readable", result.content)
            self.assertTrue(result.trusted)

    def test_index_write_failure_leaves_no_partial_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_b7_index_crash_") as directory:
            root = Path(directory)
            (root / "source.txt").write_text("index", encoding="utf-8")
            store, session_id = make_store(directory)
            builder = ArtifactIndexBuilder(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                producer_role="planner",
            )
            records = builder.build(
                [
                    {
                        "artifact_id": "A",
                        "kind": "file",
                        "locator": "source.txt",
                        "authority": "GENERATED_ARTIFACT",
                        "approval_status": "UNKNOWN",
                    }
                ]
            )
            with patch.object(store, "transaction", side_effect=OSError("index crash")):
                with self.assertRaisesRegex(OSError, "index crash"):
                    DerivedRuntimeStore(store).write_artifact_snapshot(
                        session_id=session_id,
                        project_id="test_runtime",
                        project_revision=1,
                        policy_hash="policy-v1",
                        records=[record.to_dict() for record in records],
                    )
            connection = store.raw_connection()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) AS count FROM f14_artifact_index_snapshots"
                ).fetchone()["count"]
            finally:
                connection.close()
            self.assertEqual(0, count)

    def test_symlink_escape_is_stale_or_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_b7_symlink_") as directory:
            root = Path(directory) / "root"
            root.mkdir()
            outside = Path(directory) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            link = root / "link.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("当前 Windows 环境不允许创建测试符号链接")
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
            )
            with self.assertRaisesRegex(RuntimeValidationError, "PATH_ANOMALY"):
                cache.read("link.txt")

    def test_unknown_graph_edge_never_becomes_not_affected(self) -> None:
        graph = DependencyGraph(
            [
                DependencyEdge(
                    graph_kind="execution",
                    edge_type="affects",
                    source="file:similar_name.py",
                    target="AC-7",
                    evidence_ref="unknown",
                    source_hash="",
                    revision=1,
                    confidence="unknown",
                )
            ]
        )
        self.assertEqual(0, len(graph.explicit_edges))
        self.assertNotIn("not_affected", graph.to_dicts()[0].values())

    def test_cache_cannot_write_project_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_b7_state_") as directory:
            root = Path(directory)
            project = root / "project.yaml"
            project.write_text("status: PLANNING\n", encoding="utf-8")
            before = project.read_bytes()
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
            )
            cache.read("project.yaml")
            self.assertEqual(before, project.read_bytes())


if __name__ == "__main__":
    unittest.main()
