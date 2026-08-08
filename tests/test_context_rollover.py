"""Stage 6 Runtime Session 与 Model Invocation rollover 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.context import (
    ContextBuildRequest,
    ContextBuilder,
    ContextRolloverService,
    InvocationStats,
    RolloverHandoff,
    RolloverPolicy,
    evaluate_rollover,
)
from runtime.errors import RuntimeValidationError
from runtime.event_types import EventType
from tests.test_formal_context_builder import _prepare_project


def _handoff(context_id: str) -> dict[str, list[str]]:
    return {
        "completed": ["已完成核心流程分析"],
        "current_state": ["项目仍处于 IMPLEMENTING", "当前 revision 可从 Durable State 读取"],
        "files_changed": ["code/main.py"],
        "verification_completed": ["单元测试通过"],
        "open_issues": [],
        "known_failures": [],
        "next_actions": ["继续执行浏览器验收"],
        "important_decisions": ["保持 approved_plan 不变"],
        "do_not_repeat": ["不要重复已完成的单元测试"],
        "references": [context_id, "project.yaml", "memory/plans/plan-001.md"],
    }


def _context(tmp_path: Path):
    store, session_id, run_id, root = _prepare_project(
        tmp_path, "IMPLEMENTING", "generator"
    )
    context = ContextBuilder(store).build(
        ContextBuildRequest(session_id, run_id, "generator", ("code/main.py",))
    )
    return store, session_id, run_id, root, context


def test_rollover_is_runtime_determined_and_ignores_model_self_report(tmp_path: Path) -> None:
    _, _, _, _, context = _context(tmp_path)
    policy = RolloverPolicy(
        context_budget_percent=100,
        max_tool_calls=2,
        max_compactions=3,
        max_elapsed_seconds=1800,
    )
    assert not evaluate_rollover(context, InvocationStats(), policy=policy).required
    decision = evaluate_rollover(
        context, InvocationStats(tool_call_count=2), policy=policy
    )
    assert decision.required
    assert decision.reason_codes == ("tool_call_threshold",)


def test_rollover_handoff_is_durable_and_fresh_invocation_rebuilds_f13_context(
    tmp_path: Path,
) -> None:
    store, session_id, first_run, root, context = _context(tmp_path)
    service = ContextRolloverService(
        store,
        rollover_policy=RolloverPolicy(max_tool_calls=1),
    )
    first_invocation = service.start_invocation(
        context, idempotency_key="model-a"
    )
    handoff = service.rollover(
        context,
        InvocationStats(tool_call_count=1),
        _handoff(context.context_id),
        invocation_id=first_invocation["invocation_id"],
        idempotency_key="rollover-a",
    )
    assert handoff["handoff_id"]
    assert store.get_model_invocation(
        session_id, first_invocation["invocation_id"]
    )["status"] == "ROLLED_OVER"
    stored = store.get_rollover_handoff(session_id, handoff["handoff_id"])
    assert stored["handoff"]["next_actions"] == ["继续执行浏览器验收"]
    assert "print('ok')" not in str(stored)

    next_run = store.create_role_run(session_id, "worker-fresh", "generator")
    fresh = service.start_fresh_invocation(
        session_id,
        first_invocation["invocation_id"],
        next_run,
        additional_references=("code/main.py",),
        idempotency_key="model-b",
    )
    assert fresh.invocation["previous_invocation_id"] == first_invocation["invocation_id"]
    assert fresh.invocation["status"] == "ACTIVE"
    assert fresh.handoff["references"][0] == context.context_id
    assert fresh.context.project_state_hash == context.project_state_hash
    assert [(item.reference, item.content_hash) for item in fresh.context.sources] == [
        (item.reference, item.content_hash) for item in context.sources
    ]
    assert root.joinpath("project.yaml").is_file()
    events = store.list_events(session_id)
    event_types = [event.event_type for event in events]
    assert EventType.MODEL_INVOCATION_STARTED in event_types
    assert EventType.MODEL_INVOCATION_ROLLED_OVER in event_types
    rollover_event = [
        event for event in events if event.event_type == EventType.MODEL_INVOCATION_ROLLED_OVER
    ][0]
    assert "继续执行浏览器验收" not in str(rollover_event.payload)


def test_rollover_is_idempotent_and_old_handoff_is_not_rewritten(tmp_path: Path) -> None:
    store, _, _, _, context = _context(tmp_path)
    service = ContextRolloverService(
        store, rollover_policy=RolloverPolicy(max_tool_calls=1)
    )
    invocation = service.start_invocation(context, idempotency_key="model-a")
    first = service.rollover(
        context,
        InvocationStats(tool_call_count=1),
        _handoff(context.context_id),
        invocation_id=invocation["invocation_id"],
        idempotency_key="rollover-a",
    )
    second = service.rollover(
        context,
        InvocationStats(tool_call_count=1),
        _handoff(context.context_id),
        invocation_id=invocation["invocation_id"],
        idempotency_key="rollover-a",
    )
    assert first["handoff_id"] == second["handoff_id"]
    events = store.list_events(context.session_id)
    assert len([event for event in events if event.event_type == EventType.MODEL_INVOCATION_ROLLED_OVER]) == 1


def test_handoff_requires_state_and_reference_and_rejects_secret(tmp_path: Path) -> None:
    store, _, _, _, context = _context(tmp_path)
    service = ContextRolloverService(
        store, rollover_policy=RolloverPolicy(max_tool_calls=1)
    )
    invocation = service.start_invocation(context, idempotency_key="model-a")
    invalid = _handoff("wrong-context")
    with pytest.raises(RuntimeValidationError, match="ROLLOVER_CONTEXT_REFERENCE_MISSING"):
        service.rollover(
            context,
            InvocationStats(tool_call_count=1),
            invalid,
            invocation_id=invocation["invocation_id"],
            idempotency_key="rollover-a",
        )
    invalid = _handoff(context.context_id)
    invalid["important_decisions"] = ["api_key: should-not-be-stored"]
    with pytest.raises(RuntimeValidationError, match="ROLLOVER_HANDOFF_SECRET_FORBIDDEN"):
        RolloverHandoff.from_mapping(invalid)


def test_fresh_invocation_requires_completed_handoff(tmp_path: Path) -> None:
    store, session_id, first_run, _, context = _context(tmp_path)
    service = ContextRolloverService(store)
    invocation = service.start_invocation(context, idempotency_key="model-a")
    next_run = store.create_role_run(session_id, "worker-fresh", "generator")
    with pytest.raises(RuntimeValidationError, match="ROLLOVER_HANDOFF_MISSING"):
        service.start_fresh_invocation(
            session_id,
            invocation["invocation_id"],
            next_run,
            idempotency_key="model-b",
        )
