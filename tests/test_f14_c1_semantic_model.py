"""F14-C1 语义模型的确定性与 F13 正式 Context 隔离测试。"""

from __future__ import annotations

from runtime.context import ContextBuildRequest, ContextBuilder
from tests.test_formal_context_builder import _prepare_project


def test_c1_semantic_model_is_role_task_revision_scoped_without_delivery_change(tmp_path) -> None:
    store, session_id, run_id, _ = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    request = ContextBuildRequest(
        session_id,
        run_id,
        "generator",
        task_identity="change-dashboard:generator:task-012",
        dependency_roots={
            "requirements": ("REQ-007",),
            "acceptance_criteria": ("AC-014",),
            "plan_tasks": ("TASK-012",),
        },
        global_constraints=("SECURITY-002", "PRIVACY-001"),
        task_constraints=("TASK-012",),
        approval_constraints=("APPROVAL-SCOPE-001",),
        safety_constraints=("DATA-LOCALITY-001",),
    )
    builder = ContextBuilder(store)
    current = builder.build(request)
    semantic = builder.build_semantic_model(request, current=current)
    repeat = builder.build_semantic_model(request, current=current)

    assert current.context_hash == builder.build(request).context_hash
    assert semantic.model_hash == repeat.model_hash
    assert semantic.task_identity == "change-dashboard:generator:task-012"
    assert semantic.role == "generator"
    assert semantic.project_revision == current.project_revision
    assert set(semantic.context_classes) == {
        "mandatory",
        "task_relevant",
        "on_demand",
        "omitted",
        "unknown",
    }
    assert semantic.constraints["global_constraints"] == (
        "SECURITY-002",
        "PRIVACY-001",
    )
    assert all(unit.role_scope == "generator" for units in semantic.context_classes.values() for unit in units)
    assert "task_identity" not in current.manifest

