"""R2 Core Module 的隔离测试。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from runtime.control_plane import initialize_control_plane
from runtime.reference_analysis import ReferenceAnalysisModule
from runtime.reference_analysis.errors import ReferenceAnalysisCrash, ReferenceAnalysisError
from runtime.reference_analysis.registry import ReferenceRegistry
from runtime.execution.path_policy import PathAccessDenied
from runtime.orchestrator import Orchestrator
from scripts.project_migration import preview_runtime_migration
from scripts.project_state import load_project_state, serialize_project_state, validate_project_state
from tests.test_project_migration import v4_state
from runtime.session_store import SessionStore


DOMAINS = (
    "product", "information_architecture", "navigation", "interaction", "layout",
    "visual_style", "components", "design_tokens", "motion", "content_style", "brand",
    "technical_architecture",
)


def _scope(**overrides: str) -> dict[str, str]:
    result = {domain: "unspecified" for domain in DOMAINS}
    result.update(overrides)
    return result


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "test_reference_analysis_project"
    root.mkdir()
    state = v4_state("REFERENCE_ANALYSIS")
    state.update(
        {
            "project_id": "test_reference_analysis_project",
            "next_role": None,
            "active_module": "reference_analysis",
            "requirements_status": "sufficient_for_planning",
            "active_requirements": "memory/requirements/requirements_v001.yaml",
            "reference_status": "ready",
            "reference_analysis_status": "running",
            "active_reference_synthesis": None,
        }
    )
    requirement = root / state["active_requirements"]
    requirement.parent.mkdir(parents=True)
    requirement.write_text("用户需要一个可追溯的参考分析输入。\n", encoding="utf-8")
    control_home = tmp_path / "control-plane"
    control_plane = initialize_control_plane(state["project_id"], home=control_home)
    session = SessionStore(control_plane / "sessions.sqlite3").create_session(
        state["project_id"], root, idempotency_key="r2-test-session"
    )
    preview = preview_runtime_migration(state, project_root=root, session_id=session.session_id)
    (root / "project.yaml").write_text(serialize_project_state(preview), encoding="utf-8")
    (root / ".test-control-plane-home").write_text(str(control_home), encoding="utf-8")
    assert validate_project_state(load_project_state(root / "project.yaml"), root) == []
    return root


def _module(root: Path) -> ReferenceAnalysisModule:
    control_home = Path((root / ".test-control-plane-home").read_text(encoding="utf-8"))
    return ReferenceAnalysisModule(
        root,
        orchestrator=Orchestrator(root, control_plane_home=control_home),
    )


def _register_text(root: Path, *, scope: dict[str, str] | None = None) -> ReferenceAnalysisModule:
    text = root / "memory" / "requirements" / "reference-input.txt"
    text.write_text("这是一个面向用户的产品，包含导航、卡片布局和按钮交互。\n", encoding="utf-8")
    module = _module(root)
    module.register_source(
        {
            "source_type": "text_description",
            "source": {"text_ref": "memory/requirements/reference-input.txt"},
            "reference_mode": "adaptation",
            "source_origin": {"type": "user_provided"},
        },
        requested_scope=scope or _scope(product="include", navigation="exclude", layout="include"),
    )
    return module


def test_text_end_to_end_and_project_cas(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    result = _register_text(root).run(worker_id="r2-worker")
    assert result["status"] == "completed"
    state = load_project_state(root / "project.yaml")
    assert state["status"] == "PLANNING"
    assert state["next_role"] == "planner"
    assert state["active_reference_synthesis"].startswith("memory/references/")
    assert (root / state["active_reference_synthesis"]).is_file()


def test_scope_exclusion_and_requirement_priority_are_explicit(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    module = _register_text(root, scope=_scope(product="include", navigation="exclude"))
    result = module.run()
    synthesis = module.store.read(result["synthesis_ref"])
    assert all(item["domain"] != "navigation" for item in synthesis["decisions"]["adopt"] + synthesis["decisions"]["adapt"])
    assert synthesis["priority_policy"]["explicit_user_requirements_override_reference"] is True
    assert synthesis["priority_policy"]["reference_decision_is_not_requirement"] is True


def test_image_registration_is_safe_and_semantic_analysis_is_unavailable(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    image = root / "artifacts" / "references" / "input.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    module = _module(root)
    module.register_source(
        {
            "source_type": "image",
            "source": {"artifact_ref": "artifacts/references/input.png"},
            "reference_mode": "inspiration",
            "source_origin": {"type": "user_uploaded"},
        },
        requested_scope=_scope(visual_style="include"),
    )
    result = module.run()
    synthesis = module.store.read(result["synthesis_ref"])
    assert synthesis["unknown"]
    assert all("adopt" not in item["reason"] for item in synthesis["unknown"])


def test_image_path_escape_and_web_unsafe_scheme_fail_closed(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    module = _module(root)
    with pytest.raises(PathAccessDenied):
        module.path_policy.assert_module_path("reference_analysis", root, "../outside.png", operation="read")
    with pytest.raises(ReferenceAnalysisError) as exc:
        from runtime.reference_analysis.adapters.web_page import WebPageAdapter

        WebPageAdapter(config=ReferenceRegistry().config).normalize(
            {
                "reference_id": "REF-001",
                "scope_ref": "memory/references/reference-001/scope-001.yaml",
                "context": {"type": "new_project", "project_id": "test_reference_analysis_project"},
                "source": {"uri": "http://example.com"},
            },
            root=root,
            path_policy=module.path_policy,
        )
    assert exc.value.code == "WEB_UNSAFE_SCHEME"


def test_registry_is_config_driven_and_unknown_adapter_fails_closed() -> None:
    registry = ReferenceRegistry()
    assert registry.supported_source_types() == {"text_description", "image", "web_page"}
    with pytest.raises(ReferenceAnalysisError):
        registry.adapter("unknown_source_type")


def test_multiple_sources_record_conflict_without_silent_choice(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    text_one = root / "memory" / "requirements" / "one.txt"
    text_two = root / "memory" / "requirements" / "two.txt"
    text_one.write_text("产品使用卡片布局。\n", encoding="utf-8")
    text_two.write_text("产品使用两栏布局。\n", encoding="utf-8")
    module = _module(root)
    for name in ("one.txt", "two.txt"):
        module.register_source(
            {
                "source_type": "text_description",
                "source": {"text_ref": f"memory/requirements/{name}"},
                "reference_mode": "inspiration",
                "source_origin": {"type": "user_provided"},
            },
            requested_scope=_scope(layout="include"),
        )
    synthesis = module.store.read(module.run()["synthesis_ref"])
    assert synthesis["conflicts"]
    assert synthesis["conflicts"][0]["resolution_status"] == "requires_planner_resolution"


def test_idempotent_rerun_and_crash_recovery_do_not_duplicate_artifacts(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    module = _register_text(root)
    before = load_project_state(root / "project.yaml")
    with pytest.raises(ReferenceAnalysisCrash):
        module.run(fail_at="after_artifact_commit_before_cas")
    assert load_project_state(root / "project.yaml") == before
    retry = module.run()
    second = module.run()
    assert retry["synthesis_ref"] == second["synthesis_ref"]
    assert len(list((root / "memory" / "references" / "synthesis").glob("*.yaml"))) == 1


def test_no_reference_workflow_waits_and_core_roles_remain_three(tmp_path: Path) -> None:
    root = _make_project(tmp_path)
    state = load_project_state(root / "project.yaml")
    state["status"] = "WAITING_FOR_USER"
    state["active_module"] = None
    state["next_role"] = None
    (root / "project.yaml").write_text(serialize_project_state(state), encoding="utf-8")
    control_home = Path((root / ".test-control-plane-home").read_text(encoding="utf-8"))
    started = Orchestrator(root, control_plane_home=control_home).start()
    assert started["selection"].kind == "WAIT"
    assert {"planner", "generator", "evaluator"} <= set(
        __import__("runtime.policy", fromlist=["load_role_capabilities"]).load_role_capabilities()
    )
