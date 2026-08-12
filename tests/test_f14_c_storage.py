"""F14-C 语义、Shadow 和候选工件的 F10 派生存储测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from runtime.deterministic.store import DerivedRuntimeStore
from runtime.errors import RuntimeStorageError
from tests.runtime_test_support import make_store


def test_c1_c5_derived_records_are_append_only_and_integrity_checked() -> None:
    with tempfile.TemporaryDirectory(prefix="test_f14_c_storage_") as directory:
        store, session_id = make_store(directory)
        derived = DerivedRuntimeStore(store)
        semantic_id = derived.write_context_semantic_snapshot(
            session_id=session_id,
            run_id="run-1",
            project_id="test_runtime",
            role="generator",
            project_revision=1,
            semantic={"model_hash": "a" * 64, "task_identity": "task-1"},
            semantic_id="semantic-fixed",
        )
        comparison_id = derived.write_shadow_comparison(
            session_id=session_id,
            run_id="run-1",
            project_id="test_runtime",
            role="generator",
            project_revision=1,
            comparison={"comparison_hash": "b" * 64, "mandatory_total": 1},
            comparison_id="comparison-fixed",
        )
        gate_id = derived.write_shadow_gate_result(
            session_id=session_id,
            run_id="run-1",
            comparison_id=comparison_id,
            gate={"result": "PASS", "comparison_hash": "b" * 64},
            gate_id="gate-fixed",
        )
        evaluation_id = derived.write_selective_candidate(
            session_id=session_id,
            run_id="run-1",
            evaluation={"candidate_id": "candidate-1", "eligible": True},
            evaluation_id="evaluation-fixed",
        )

        assert semantic_id == "semantic-fixed"
        assert gate_id == "gate-fixed"
        assert evaluation_id == "evaluation-fixed"
        assert derived.read_context_semantic_snapshot(session_id, semantic_id)["result"]["task_identity"] == "task-1"

        connection = store.raw_connection()
        try:
            connection.execute("DROP TRIGGER f14_context_semantic_no_update")
            connection.execute(
                "UPDATE f14_context_semantic_snapshots SET semantic_json=? WHERE semantic_id=?",
                ("{\"tampered\":true}", semantic_id),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(RuntimeStorageError, match="SEMANTIC_CORRUPT"):
            derived.read_context_semantic_snapshot(session_id, semantic_id)

