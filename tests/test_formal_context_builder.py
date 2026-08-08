"""F13.1 正式 Context Builder 的确定性与边界测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.errors import RuntimeValidationError
from runtime.event_types import EventType
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state
from tests.runtime_test_support import make_runtime_project, open_runtime_store


def _prepare_project(tmp_path: Path, status: str, role: str) -> tuple[SessionStore, str, str, Path]:
    root, session_id = make_runtime_project(tmp_path, status="PLANNING")
    store = open_runtime_store(root)
    state = load_project_state(root / "project.yaml")
    state["status"] = status
    state["next_role"] = role
    state["active_module"] = None
    references = {
        "active_requirements": "memory/requirements/requirements_v001.yaml",
        "approved_plan": "memory/plans/plan-001.md",
        "active_product_spec": "memory/specifications/spec-001.md",
        "last_issue_package": "evaluation/issues/issue-001.md",
        "last_evaluation": "evaluation/reports/evaluation-001.md",
        "last_generator_response": "memory/handoffs/responses/response-001.md",
        "evidence_manifest": "evaluation/evidence/evaluation-001/manifest.md",
    }
    for field, reference in references.items():
        state[field] = reference
        target = root / reference
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{field}: safe\n", encoding="utf-8")
    state["evaluation_profile"] = "default"
    (root / "code").mkdir(exist_ok=True)
    (root / "code/main.py").write_text("print('ok')\n", encoding="utf-8")
    runtime_state = preview_runtime_migration(
        state, project_root=root, session_id=session_id
    )
    (root / "project.yaml").write_text(
        serialize_project_state(runtime_state), encoding="utf-8"
    )
    run_id = store.create_role_run(session_id, "worker-context", role)
    return store, session_id, run_id, root


def test_role_specific_sources_include_planner_generator_evaluator_inputs(tmp_path: Path) -> None:
    planner_store, planner_session, planner_run, planner_root = _prepare_project(
        tmp_path / "planner", "PLANNING", "planner"
    )
    planner = ContextBuilder(planner_store).build(
        ContextBuildRequest(planner_session, planner_run, "planner")
    )
    generator_store, generator_session, generator_run, _ = _prepare_project(
        tmp_path / "generator", "IMPLEMENTING", "generator"
    )
    generator = ContextBuilder(generator_store).build(
        ContextBuildRequest(generator_session, generator_run, "generator")
    )
    evaluator_store, evaluator_session, evaluator_run, _ = _prepare_project(
        tmp_path / "evaluator", "EVALUATING", "evaluator"
    )
    evaluator = ContextBuilder(evaluator_store).build(
        ContextBuildRequest(evaluator_session, evaluator_run, "evaluator")
    )

    planner_refs = {source.reference for source in planner.sources}
    generator_refs = {source.reference for source in generator.sources}
    evaluator_refs = {source.reference for source in evaluator.sources}
    assert "memory/requirements/requirements_v001.yaml" in planner_refs
    assert "memory/plans/plan-001.md" in generator_refs
    assert "evaluation/evidence/evaluation-001/manifest.md" in evaluator_refs
    assert planner_refs != generator_refs != evaluator_refs
    assert all(source.content_hash for source in planner.sources)
    assert all(source.reason for source in evaluator.sources)
    assert planner_root.joinpath("project.yaml").is_file()


def test_role_state_mismatch_and_unknown_role_are_denied(tmp_path: Path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_ROLE_STATE_MISMATCH"):
        ContextBuilder(store).build(
            ContextBuildRequest(session_id, run_id, "evaluator")
        )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_UNKNOWN_ROLE"):
        ContextBuilder(store).build(ContextBuildRequest(session_id, run_id, "architect"))


def test_single_project_boundary_and_control_plane_are_enforced(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_REFERENCE_INVALID"):
        ContextBuilder(store).build(
            ContextBuildRequest(
                session_id,
                run_id,
                "generator",
                (str(tmp_path / "other" / "file.md"),),
            )
        )
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_NOT_ALLOWED"):
        ContextBuilder(store).build(
            ContextBuildRequest(session_id, run_id, "generator", (".runtime/sessions.sqlite3",))
        )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_PROJECT_BOUNDARY"):
        from runtime.context import build_context

        build_context(store, session_id, run_id, "generator", project_root=tmp_path / "other")
    assert root.joinpath("project.yaml").is_file()


def test_deterministic_ordering_hash_and_revision_change(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store)
    first = builder.build(
        ContextBuildRequest(session_id, run_id, "generator", ("code/main.py",))
    )
    second = builder.build(
        ContextBuildRequest(session_id, run_id, "generator", ("code/main.py",))
    )
    assert first.context_hash == second.context_hash
    assert [item.reference for item in first.sources] == [
        item.reference for item in second.sources
    ]
    state = load_project_state(root / "project.yaml")
    state["runtime"]["revision"] = 1
    (root / "project.yaml").write_text(
        serialize_project_state(state), encoding="utf-8"
    )
    changed = builder.build(
        ContextBuildRequest(session_id, run_id, "generator", ("code/main.py",))
    )
    assert changed.context_hash != first.context_hash
    assert changed.project_revision == 1


def test_safe_context_and_lightweight_f10_audit(tmp_path: Path) -> None:
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    secret_file = root / "memory/requirements/secret.md"
    secret_file.write_text("API_KEY=do-not-expose\n", encoding="utf-8")
    with pytest.raises(RuntimeValidationError, match="CONTEXT_SECRET_FORBIDDEN"):
        ContextBuilder(store).build(
            ContextBuildRequest(session_id, run_id, "generator", ("memory/requirements/secret.md",))
        )
    package = ContextBuilder(store).build(
        ContextBuildRequest(session_id, run_id, "generator")
    )
    event = [
        item
        for item in store.list_events(session_id)
        if item.event_type == EventType.CONTEXT_BUILT
    ][-1]
    assert event.payload["context_id"] == package.context_id
    assert "sources" not in event.payload
    assert "do-not-expose" not in str(event.payload)
