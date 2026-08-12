"""R3 First-Ask 与 Planner/Context 集成测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from runtime.context import ContextBuildRequest, ContextBuilder
from runtime.control_plane import initialize_control_plane
from runtime.intake import FirstAskIntakeModule, detect_references
from runtime.orchestrator import Orchestrator
from runtime.reference_analysis import ReferenceAnalysisModule
from runtime.session_store import SessionStore
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state, validate_project_state
from scripts.reference_planner import (
    render_reference_integration,
    validate_proposal_reference_traceability,
)
from tests.test_project_migration import v4_state


def _project(tmp_path: Path, *, sufficient: bool = True) -> tuple[Path, Path]:
    root = tmp_path / "test_r3_reference_intake"
    root.mkdir()
    state = v4_state("INTAKE")
    state.update(
        {
            "project_id": "test_r3_reference_intake",
            "active_module": "first_ask_intake",
            "requirements_status": "sufficient_for_planning" if sufficient else "draft",
            "requirements_version": 1 if sufficient else 0,
            "active_requirements": "memory/requirements/requirements_v001.yaml" if sufficient else None,
            "reference_status": "none",
            "reference_analysis_status": "not_started",
            "active_reference_synthesis": None,
        }
    )
    if sufficient:
        requirement = root / "memory" / "requirements" / "requirements_v001.yaml"
        requirement.parent.mkdir(parents=True)
        requirement.write_text(
            yaml.safe_dump(
                {
                    "schema_version": 1,
                    "requirement_version": 1,
                    "project_id": state["project_id"],
                    "references": [],
                    "primary_goal": {"value": "完成一个可规划的应用", "status": "answered"},
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    control_home = tmp_path / "control-plane"
    session = SessionStore(initialize_control_plane(state["project_id"], home=control_home) / "sessions.sqlite3").create_session(
        state["project_id"], root, idempotency_key="r3-test-session"
    )
    preview = preview_runtime_migration(state, project_root=root, session_id=session.session_id)
    (root / "project.yaml").write_text(serialize_project_state(preview), encoding="utf-8")
    (root / ".test-control-plane-home").write_text(str(control_home), encoding="utf-8")
    assert validate_project_state(load_project_state(root / "project.yaml"), root) == []
    return root, control_home


def test_reference_detection_is_conservative_and_preserves_scope() -> None:
    candidates = detect_references("请参考 https://example.com 的布局，只借鉴视觉，不要暗色主题")
    assert len(candidates) == 1
    assert candidates[0].source_type == "web_page"
    assert candidates[0].reference_mode == "inspiration"
    assert candidates[0].requested_scope["layout"] == "include"
    assert candidates[0].requested_scope["visual_style"] == "exclude"
    assert "dark_theme" in candidates[0].excluded_details
    assert detect_references("以后可能参考别的产品") == ()
    named = detect_references("参考 Linear 的导航")
    assert named[0].source_type == "text_description"


def test_first_ask_registers_and_routes_without_analysis(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    runtime = Orchestrator(root, control_plane_home=control_home)
    module = FirstAskIntakeModule(root, orchestrator=runtime)
    result = module.process_user_message("参考 Linear 的布局，整体重新设计，不要暗色主题", worker_id="r3-first-ask")
    assert result.route == "REFERENCE_ANALYSIS"
    state = load_project_state(root / "project.yaml")
    assert state["active_module"] == "reference_analysis"
    assert state["reference_analysis_status"] == "running"
    assert len(list((root / "memory" / "references").rglob("source-*.yaml"))) == 1
    assert not list((root / "memory" / "references").rglob("analysis-*.yaml"))
    requirement = yaml.safe_load((root / state["active_requirements"]).read_text(encoding="utf-8"))
    assert requirement["references"][0]["reference_id"] == result.reference_ids[0]
    assert requirement["references"][0]["explicit_exclusions"] == ["layout"] or "visual_style" in requirement["references"][0]["explicit_exclusions"]

    duplicate = module.process_user_message("参考 Linear 的布局，整体重新设计，不要暗色主题", worker_id="r3-first-ask-2")
    assert duplicate.idempotent is True
    assert duplicate.reference_ids == result.reference_ids
    assert len(list((root / "memory" / "references").rglob("source-*.yaml"))) == 1


def test_no_reference_keeps_old_planning_route(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    result = FirstAskIntakeModule(root, orchestrator=Orchestrator(root, control_plane_home=control_home)).process_user_message(
        "做一个待办 App，目标是记录任务", worker_id="r3-no-reference"
    )
    assert result.route == "PLANNING"
    state = load_project_state(root / "project.yaml")
    assert state["status"] == "PLANNING"
    assert state["next_role"] == "planner"
    assert state["active_module"] is None
    assert not list((root / "memory" / "references").rglob("source-*.yaml"))


def test_planner_reference_integration_requires_traceable_decisions() -> None:
    synthesis = {
        "source_references": ["REF-001"],
        "decisions": {"adopt": [{"decision_id": "REFDEC-001", "domain": "layout", "rationale": "保留层级"}], "adapt": [], "avoid": []},
        "unknown": [],
        "conflicts": [],
    }
    rendered = render_reference_integration(synthesis, selections={"adopt": [synthesis["decisions"]["adopt"][0]], "adapt": [], "avoid": []})
    assert "REF-001" in rendered and "REFDEC-001" in rendered
    assert validate_proposal_reference_traceability(rendered, synthesis) == []
    assert validate_proposal_reference_traceability("# Reference Integration\nREFDEC-999", synthesis)


def test_reference_module_context_uses_catalog_not_raw_sources(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    runtime = Orchestrator(root, control_plane_home=control_home)
    FirstAskIntakeModule(root, orchestrator=runtime).process_user_message("参考 Linear 的导航", worker_id="r3-context")
    state = load_project_state(root / "project.yaml")
    session_id = state["runtime"]["session_id"]
    store = runtime.store
    package = ContextBuilder(store).build(ContextBuildRequest(session_id, "r3-module-context", "reference_analysis"))
    catalog = next(source for source in package.sources if source.source_type == "reference_catalog")
    assert catalog.inline_content is not None
    assert "reference_mode" in catalog.inline_content
    assert "analysis-" not in catalog.inline_content


def test_reference_revocation_is_append_only(tmp_path: Path) -> None:
    root, control_home = _project(tmp_path)
    module = ReferenceAnalysisModule(root, orchestrator=Orchestrator(root, control_plane_home=control_home))
    for name in ("Linear", "Notion"):
        module.register_source(
            {
                "source_type": "text_description",
                "source": {"text_ref": "memory/requirements/requirements_v001.yaml", "identifier": name},
                "reference_mode": "adaptation",
                "source_origin": {"type": "user_provided"},
            },
            requested_scope={
                domain: "include" if domain == "layout" else "unspecified"
                for domain in ("product", "information_architecture", "navigation", "interaction", "layout", "visual_style", "components", "design_tokens", "motion", "content_style", "brand", "technical_architecture")
            },
        )
    result = module.revoke_reference("REF-001", reason="用户撤回第一个参考")
    assert result["status"] == "superseded"
    active = module.store.list_sources({"type": "new_project", "project_id": "test_r3_reference_intake", "change_request_id": None})
    assert [item["reference_id"] for item in active] == ["REF-002"]
    assert (root / "memory" / "references" / "reference-001" / "source-001.yaml").is_file()
    assert (root / "memory" / "references" / "reference-001" / "source-002.yaml").is_file()
