"""F14-C C0 Preflight Gate 的边界证据测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.deterministic.artifact_index import ArtifactIndexBuilder
from runtime.errors import RuntimeValidationError
from runtime.control_plane import session_database_path
from runtime.orchestrator import Orchestrator
from scripts.project_state import load_project_state
from tests.runtime_test_support import make_runtime_project, open_runtime_store
from tests.test_phase_runner import FakeModel, _runner


def test_c0_control_plane_path_is_outside_project_workspace(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    database = session_database_path(
        "test_c0_boundary",
        home=tmp_path / "external-control-plane",
    )
    assert not database.is_relative_to(root)
    assert not (root / ".runtime" / "sessions.sqlite3").exists()


def test_c0_authority_cannot_be_spoofed_by_locator_or_revision(tmp_path: Path) -> None:
    root, _ = make_runtime_project(tmp_path)
    store = open_runtime_store(root)
    active = root / "memory/requirements/requirements_v001.yaml"
    active.write_text("requirements: []\n", encoding="utf-8")
    (root / "fake-requirements.yaml").write_text("fake\n", encoding="utf-8")
    builder = ArtifactIndexBuilder(
        root,
        project_revision=0,
        policy_hash="policy-v1",
        producer_role="planner",
    )
    with pytest.raises(RuntimeValidationError, match="AUTHORITY_BINDING_MISMATCH"):
        builder.index_file(
            artifact_id="fake",
            kind="requirements",
            locator="fake-requirements.yaml",
            authority="APPROVED_REQUIREMENT",
            approval_status="APPROVED",
            source_state_ref="active_requirements",
        )

    with pytest.raises(RuntimeValidationError, match="AUTHORITY_REVISION_MISMATCH"):
        ArtifactIndexBuilder(
            root,
            project_revision=1,
            policy_hash="policy-v1",
            producer_role="planner",
        ).index_file(
            artifact_id="active",
            kind="requirements",
            locator="memory/requirements/requirements_v001.yaml",
            authority="APPROVED_REQUIREMENT",
            approval_status="APPROVED",
            source_state_ref="active_requirements",
        )
    assert store.get_session(str(load_project_state(root / "project.yaml")["runtime"]["session_id"]))


def test_c0_real_model_boundary_counts_adapter_failure_as_llm(tmp_path: Path) -> None:
    class FailingModel(FakeModel):
        def invoke(self, request):
            raise RuntimeError("adapter failed after request boundary")

    root, session_id, orchestrator, started, runner = _runner(tmp_path, FailingModel())
    result = runner.run(
        session_id,
        started["run_id"],
        started["lease_token"] or "",
        idempotency_key="c0-boundary-failure",
    )
    assert result.status == "FAILED"
    connection = orchestrator.store.raw_connection()
    try:
        row = connection.execute(
            "SELECT model_json FROM f14_telemetry WHERE session_id=?",
            (session_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    model = json.loads(row["model_json"])
    assert model["model_efficiency"]["real_llm_invocations"] == 1
    assert model["execution_types"]["llm"] == 1
