"""F13.2 Context Budget、Priority 与 Role Scoping 测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.context import ContextBuildRequest, ContextBuilder, ContextPolicy
from runtime.errors import RuntimeValidationError
from runtime.event_types import EventType
from tests.test_formal_context_builder import _prepare_project


def _rule_lines(rule: tuple[str, str, str, str]) -> list[str]:
    source_type, value, priority, delivery_mode = rule
    lines = [f"      - source_type: {source_type}"]
    if source_type == "project_state":
        lines.append(f"        reference: {value}")
    else:
        lines.append(f"        field: {value}")
    lines.extend(
        [
            "        reason: budget test source",
            f"        priority: {priority}",
            f"        delivery_mode: {delivery_mode}",
        ]
    )
    return lines


def _write_policy(
    path: Path,
    *,
    max_context_bytes: int = 100000,
    max_inline_bytes: int = 100000,
    max_sources: int = 20,
    max_source_inline_bytes: int = 50000,
    generator_rules: tuple[tuple[str, str, str, str], ...] = (
        ("project_state", "project.yaml", "REQUIRED", "INLINE"),
        ("state_reference", "approved_plan", "REQUIRED", "INLINE"),
        ("state_reference", "active_product_spec", "HIGH", "INLINE"),
        ("state_reference", "last_issue_package", "NORMAL", "INLINE"),
        ("state_reference", "last_generator_response", "REFERENCE_ONLY", "INLINE"),
    ),
) -> Path:
    lines = ["version: 1", "default: deny", "roles:"]
    for role in ("planner", "generator", "evaluator"):
        lines.extend(
            [
                f"  {role}:",
                f"    max_context_bytes: {max_context_bytes if role == 'generator' else 100000}",
                f"    max_inline_bytes: {max_inline_bytes if role == 'generator' else 100000}",
                f"    max_sources: {max_sources if role == 'generator' else 20}",
                f"    max_source_inline_bytes: {max_source_inline_bytes if role == 'generator' else 50000}",
                "    sources:",
            ]
        )
        rules = (
            generator_rules
            if role == "generator"
            else (("project_state", "project.yaml", "REQUIRED", "INLINE"),)
        )
        for rule in rules:
            lines.extend(_rule_lines(rule))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _build(tmp_path: Path, policy_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store, session_id, run_id, root = _prepare_project(
        tmp_path / "project", "IMPLEMENTING", "generator"
    )
    builder = ContextBuilder(store, context_policy=ContextPolicy(policy_path))
    package = builder.build(ContextBuildRequest(session_id, run_id, "generator"))
    return package, store, session_id, root


def test_role_budgets_are_configured_independently() -> None:
    policy = ContextPolicy()
    values = [
        policy.budget_for(role).to_dict()
        for role in ("planner", "generator", "evaluator")
    ]
    assert len({tuple(value.items()) for value in values}) == 3
    assert all(value["max_context_bytes"] > 0 for value in values)


def test_required_and_high_sources_sort_before_normal(tmp_path: Path) -> None:
    policy = _write_policy(tmp_path / "context.yaml")
    package, _, _, _ = _build(tmp_path, policy)
    priorities = [source.priority for source in package.sources]
    assert priorities == sorted(
        priorities,
        key={"REQUIRED": 0, "HIGH": 1, "NORMAL": 2, "REFERENCE_ONLY": 3}.get,
    )


def test_reference_only_is_traceable_without_inline_content(tmp_path: Path) -> None:
    policy = _write_policy(tmp_path / "context.yaml")
    package, _, _, _ = _build(tmp_path, policy)
    source = next(
        item for item in package.sources if item.reference.endswith("response-001.md")
    )
    assert source.priority == "REFERENCE_ONLY"
    assert source.delivery_mode == "REFERENCE"
    assert source.inline_content is None
    assert source.content_hash and source.reason and source.size > 0


def test_normal_source_over_budget_degrades_to_reference(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path / "context.yaml",
        max_context_bytes=10000,
        max_inline_bytes=10000,
        max_source_inline_bytes=20000,
    )
    store, session_id, run_id, root = _prepare_project(
        tmp_path / "project", "IMPLEMENTING", "generator"
    )
    issue = root / "evaluation/issues/issue-001.md"
    issue.write_text("normal-source\n" * 5000, encoding="utf-8")
    package = ContextBuilder(store, context_policy=ContextPolicy(policy)).build(
        ContextBuildRequest(session_id, run_id, "generator")
    )
    source = next(item for item in package.sources if item.reference.endswith("issue-001.md"))
    assert source.priority == "NORMAL"
    assert source.delivery_mode == "REFERENCE"
    assert package.budget_used <= package.budget_limit


def test_required_over_hard_budget_fails_without_truncation(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path / "context.yaml",
        max_context_bytes=10,
        max_inline_bytes=10,
        max_source_inline_bytes=100000,
    )
    with pytest.raises(RuntimeValidationError, match="CONTEXT_REQUIRED_BUDGET_EXCEEDED"):
        _build(tmp_path, policy)


def test_source_limit_omits_non_required_source_with_hash(tmp_path: Path) -> None:
    policy = _write_policy(tmp_path / "context.yaml", max_sources=2)
    package, _, _, _ = _build(tmp_path, policy)
    assert package.source_count == 2
    assert package.omitted_source_count > 0
    omitted = package.omitted_sources[0]
    assert omitted.reference and omitted.content_hash and omitted.reason
    assert omitted.omission_reason == "SOURCE_LIMIT"


def test_per_source_inline_limit_degrades_non_required_source(tmp_path: Path) -> None:
    policy = _write_policy(
        tmp_path / "context.yaml",
        max_source_inline_bytes=10,
        generator_rules=(
            ("project_state", "project.yaml", "REQUIRED", "REFERENCE"),
            ("state_reference", "approved_plan", "REQUIRED", "REFERENCE"),
            ("state_reference", "active_product_spec", "HIGH", "INLINE"),
        ),
    )
    package, _, _, _ = _build(tmp_path, policy)
    source = next(item for item in package.sources if item.reference.endswith("spec-001.md"))
    assert source.delivery_mode == "REFERENCE"
    assert source.is_excerpt is False


def test_budget_config_changes_context_hash(tmp_path: Path) -> None:
    first_policy = _write_policy(tmp_path / "first.yaml", max_context_bytes=100000)
    second_policy = _write_policy(tmp_path / "second.yaml", max_context_bytes=100001)
    first, _, _, _ = _build(tmp_path / "first-project", first_policy)
    second, _, _, _ = _build(tmp_path / "second-project", second_policy)
    assert first.context_hash != second.context_hash


def test_audit_contains_budget_metadata_but_not_context_content(tmp_path: Path) -> None:
    policy = _write_policy(tmp_path / "context.yaml")
    package, store, session_id, _ = _build(tmp_path, policy)
    event = [
        item for item in store.list_events(session_id) if item.event_type == EventType.CONTEXT_BUILT
    ][-1]
    assert event.payload["budget_used"] == package.budget_used
    assert event.payload["budget_limit"] == package.budget_limit
    assert event.payload["omitted_source_count"] == package.omitted_source_count
    assert "content" not in str(event.payload)


def test_path_denied_source_is_rejected_before_budget_selection(tmp_path: Path) -> None:
    policy = _write_policy(tmp_path / "context.yaml")
    package, store, session_id, _ = _build(tmp_path, policy)
    with pytest.raises(RuntimeValidationError, match="EXECUTION_PATH_NOT_ALLOWED"):
        ContextBuilder(store, context_policy=ContextPolicy(policy)).build(
            ContextBuildRequest(
                session_id,
                store.get_role_run(session_id, package.run_id)["run_id"],
                "generator",
                (".runtime/sessions.sqlite3",),
            )
        )
