import tempfile
import unittest

from runtime.deterministic.dependency_graph import (
    DependencyEdge,
    DependencyGraph,
)
from runtime.deterministic.store import DerivedRuntimeStore
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from tests.runtime_test_support import make_store


HASH = "a" * 64


class F14DependencyGraphTests(unittest.TestCase):
    def test_authority_and_execution_edges_are_separate_and_persisted(self) -> None:
        graph = DependencyGraph(
            [
                DependencyEdge(
                    graph_kind="authority",
                    edge_type="approved_by",
                    source="plan:001",
                    target="approval:001",
                    evidence_ref="approval:001",
                    source_hash=HASH,
                    revision=4,
                    confidence="explicit",
                ),
                DependencyEdge(
                    graph_kind="execution",
                    edge_type="implements",
                    source="AC-007",
                    target="file:runtime/app.py",
                    evidence_ref="plan:001#AC-007",
                    source_hash=HASH,
                    revision=4,
                    confidence="explicit",
                ),
                DependencyEdge(
                    graph_kind="execution",
                    edge_type="affects",
                    source="file:unknown.py",
                    target="AC-999",
                    evidence_ref="unknown",
                    source_hash="",
                    revision=4,
                    confidence="unknown",
                ),
            ]
        )
        self.assertEqual(2, len(graph.explicit_edges))
        self.assertEqual(1, len(graph.unknown_edges))
        with tempfile.TemporaryDirectory(prefix="test_f14_graph_") as directory:
            store, session_id = make_store(directory)
            snapshot_id = graph.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
                project_revision=4,
                policy_hash="policy-v1",
            )
            loaded = DerivedRuntimeStore(store).read_dependency_snapshot(
                session_id, snapshot_id
            )
            self.assertEqual(3, loaded["edge_count"])
            self.assertEqual(
                {"authority", "execution"},
                {edge["graph_kind"] for edge in loaded["edges"]},
            )

    def test_invalid_edges_and_implicit_hard_dependencies_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeValidationError, "EDGE_TYPE_INVALID"):
            DependencyEdge(
                graph_kind="authority",
                edge_type="implements",
                source="REQ-1",
                target="file:a.py",
                evidence_ref="ref",
                source_hash=HASH,
                revision=1,
                confidence="explicit",
            )
        with self.assertRaisesRegex(RuntimeValidationError, "EVIDENCE_REQUIRED"):
            DependencyEdge(
                graph_kind="execution",
                edge_type="affects",
                source="file:a.py",
                target="AC-1",
                evidence_ref="",
                source_hash=HASH,
                revision=1,
                confidence="explicit",
            )
        graph = DependencyGraph(
            [
                DependencyEdge(
                    graph_kind="execution",
                    edge_type="affects",
                    source="filename:similar",
                    target="AC-1",
                    evidence_ref="unknown",
                    source_hash="",
                    revision=1,
                    confidence="unknown",
                )
            ]
        )
        self.assertEqual(0, len(graph.explicit_edges))

    def test_graph_corruption_blocks_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_graph_corrupt_") as directory:
            store, session_id = make_store(directory)
            graph = DependencyGraph(
                [
                    DependencyEdge(
                        graph_kind="execution",
                        edge_type="verifies",
                        source="test:test_a",
                        target="AC-1",
                        evidence_ref="evidence:1",
                        source_hash=HASH,
                        revision=1,
                        confidence="explicit",
                    )
                ]
            )
            snapshot_id = graph.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
                project_revision=1,
                policy_hash="policy-v1",
            )
            connection = store.raw_connection()
            try:
                connection.execute("DROP TRIGGER f14_dependency_edges_no_update")
                connection.execute(
                    "UPDATE f14_dependency_edges SET target=? WHERE snapshot_id=?",
                    ("AC-corrupted", snapshot_id),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeStorageError, "SNAPSHOT_CORRUPT"):
                DerivedRuntimeStore(store).read_dependency_snapshot(
                    session_id, snapshot_id
                )


if __name__ == "__main__":
    unittest.main()
