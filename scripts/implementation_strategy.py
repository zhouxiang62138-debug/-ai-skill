"""Stage 4 Planner WHAT/WHY 与 Generator HOW 的追加式策略交接。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from project_state import ProjectStateError, parse_project_yaml, serialize_project_state


STRATEGY_DIRECTORY = Path("memory") / "handoffs"
STRATEGY_PATTERN = re.compile(r"^implementation-strategy-(\d{3})\.yaml$")
STRATEGY_ID_PATTERN = re.compile(r"^implementation-strategy-(\d{3})$")
RECORD_KINDS = {"planner_what_why", "generator_how"}
WHAT_WHY_FIELDS = {"outcomes", "in_scope", "out_of_scope", "constraints", "rationale"}
IMPLEMENTATION_FIELDS = {
    "approach",
    "change_set",
    "interfaces",
    "sequence",
    "test_commands",
    "rollback",
    "risks",
}


def _error(message: str) -> ProjectStateError:
    return ProjectStateError(f"Implementation Strategy 无效：{message}")


def _nonempty_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{label} 必须是非空字符串")


def _nonempty_string_list(value: Any, label: str) -> None:
    if not isinstance(value, list) or not value:
        raise _error(f"{label} 必须是非空字符串列表")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise _error(f"{label} 只能包含非空字符串")


def _relative_reference(value: Any, label: str) -> None:
    _nonempty_string(value, label)
    path = value.replace("\\", "/")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or "../" in f"{path}/":
        raise _error(f"{label} 必须是项目内相对路径")


def _strategy_files(project_root: Path) -> list[Path]:
    directory = project_root / STRATEGY_DIRECTORY
    if not directory.is_dir():
        return []
    found: list[tuple[int, Path]] = []
    for path in directory.iterdir():
        match = STRATEGY_PATTERN.fullmatch(path.name)
        if match and path.is_file():
            found.append((int(match.group(1)), path))
    return [path for _, path in sorted(found)]


def _load_record(path: Path) -> dict[str, Any]:
    try:
        value = parse_project_yaml(path.read_text(encoding="utf-8"))
    except (OSError, ProjectStateError) as exc:
        raise _error(f"无法读取 {path.name}") from exc
    if not isinstance(value, dict):
        raise _error(f"{path.name} 顶层必须是对象")
    return value


def _validate_common(record: dict[str, Any]) -> None:
    if record.get("schema_version") != 1:
        raise _error("schema_version 必须为 1")
    strategy_id = record.get("strategy_id")
    _nonempty_string(strategy_id, "strategy_id")
    if not STRATEGY_ID_PATTERN.fullmatch(strategy_id):
        raise _error("strategy_id 格式无效")
    if record.get("record_kind") not in RECORD_KINDS:
        raise _error("record_kind 必须是 planner_what_why 或 generator_how")
    _relative_reference(record.get("source_plan"), "source_plan")


def validate_strategy_record(record: dict[str, Any]) -> None:
    """校验单条记录，并拒绝跨角色偷渡的字段。"""

    if not isinstance(record, dict):
        raise _error("记录必须是对象")
    _validate_common(record)
    kind = record["record_kind"]
    if kind == "planner_what_why":
        required = {"source_requirements", "requirement_ids", "acceptance_criteria", "what_why"}
        if not required <= set(record):
            raise _error("Planner 记录缺少需求、验收标准或 WHAT/WHY")
        _relative_reference(record["source_requirements"], "source_requirements")
        _nonempty_string_list(record["requirement_ids"], "requirement_ids")
        _nonempty_string_list(record["acceptance_criteria"], "acceptance_criteria")
        what_why = record["what_why"]
        if not isinstance(what_why, dict) or set(what_why) != WHAT_WHY_FIELDS:
            raise _error("WHAT/WHY 只能包含 outcomes、in_scope、out_of_scope、constraints、rationale")
        for field in WHAT_WHY_FIELDS:
            _nonempty_string_list(what_why[field], f"what_why.{field}")
        forbidden = {"files", "classes", "functions", "algorithms", "directories", "implementation_steps"}
        if forbidden & set(record):
            raise _error("Planner 记录不能包含低层实现字段")
        return

    if "parent_strategy_id" not in record or "implementation" not in record:
        raise _error("Generator 记录缺少 parent_strategy_id 或 HOW")
    parent = record["parent_strategy_id"]
    _nonempty_string(parent, "parent_strategy_id")
    if not STRATEGY_ID_PATTERN.fullmatch(parent):
        raise _error("parent_strategy_id 格式无效")
    implementation = record["implementation"]
    if not isinstance(implementation, dict) or set(implementation) != IMPLEMENTATION_FIELDS:
        raise _error("HOW 必须完整包含实现路线、变更、接口、顺序、测试、回滚和风险")
    _nonempty_string(implementation["approach"], "implementation.approach")
    if not isinstance(implementation["change_set"], list) or not implementation["change_set"]:
        raise _error("implementation.change_set 必须是非空列表")
    for item in implementation["change_set"]:
        if not isinstance(item, dict) or not {"path", "purpose"} <= set(item):
            raise _error("change_set 每项必须包含 path 和 purpose")
        _relative_reference(item["path"], "change_set.path")
        _nonempty_string(item["purpose"], "change_set.purpose")
    if not isinstance(implementation["interfaces"], list):
        raise _error("implementation.interfaces 必须是列表")
    _nonempty_string_list(implementation["sequence"], "implementation.sequence")
    if not isinstance(implementation["test_commands"], list) or not implementation["test_commands"]:
        raise _error("implementation.test_commands 必须是非空命令列表")
    for command in implementation["test_commands"]:
        if not isinstance(command, list) or not command or any(not isinstance(part, str) for part in command):
            raise _error("test_commands 必须使用参数数组")
    _nonempty_string(implementation["rollback"], "implementation.rollback")
    if not isinstance(implementation["risks"], list):
        raise _error("implementation.risks 必须是列表")


def _next_number(paths: list[Path]) -> int:
    return len(paths) + 1


def _write_new_record(project_root: str | Path, record: dict[str, Any]) -> Path:
    root = Path(project_root).resolve()
    directory = root / STRATEGY_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    paths = _strategy_files(root)
    expected_number = _next_number(paths)
    expected_id = f"implementation-strategy-{expected_number:03d}"
    if record.get("strategy_id") != expected_id:
        raise _error(f"下一条记录必须使用 {expected_id}")
    validate_strategy_record(record)
    target = directory / f"{expected_id}.yaml"
    payload = serialize_project_state(record).encode("utf-8")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise _error(f"拒绝覆盖已有记录：{target.name}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return target


def create_planner_strategy_record(
    project_root: str | Path,
    *,
    source_plan: str,
    source_requirements: str,
    requirement_ids: list[str],
    acceptance_criteria: list[str],
    outcomes: list[str],
    in_scope: list[str],
    out_of_scope: list[str],
    constraints: list[str],
    rationale: list[str],
) -> Path:
    """创建首条 Planner WHAT/WHY 记录，不写入任何实现细节。"""

    root = Path(project_root).resolve()
    paths = _strategy_files(root)
    if paths:
        raise _error("Planner WHAT/WHY 只能作为第一条记录创建")
    return _write_new_record(
        root,
        {
            "schema_version": 1,
            "strategy_id": "implementation-strategy-001",
            "record_kind": "planner_what_why",
            "source_plan": source_plan,
            "source_requirements": source_requirements,
            "requirement_ids": requirement_ids,
            "acceptance_criteria": acceptance_criteria,
            "what_why": {
                "outcomes": outcomes,
                "in_scope": in_scope,
                "out_of_scope": out_of_scope,
                "constraints": constraints,
                "rationale": rationale,
            },
        },
    )


def append_generator_strategy_record(
    project_root: str | Path,
    *,
    source_plan: str,
    approach: str,
    change_set: list[dict[str, Any]],
    interfaces: list[str],
    sequence: list[str],
    test_commands: list[list[str]],
    rollback: str,
    risks: list[str],
) -> Path:
    """追加 Generator HOW 记录，不能修改 Planner WHAT/WHY。"""

    root = Path(project_root).resolve()
    paths = _strategy_files(root)
    if not paths:
        raise _error("Generator HOW 必须建立在 Planner WHAT/WHY 记录之上")
    records = [_load_record(path) for path in paths]
    validate_strategy_chain(root)
    previous = records[-1]
    if previous.get("source_plan") != source_plan:
        raise _error("HOW 的 source_plan 必须与既有策略链一致")
    number = len(paths) + 1
    return _write_new_record(
        root,
        {
            "schema_version": 1,
            "strategy_id": f"implementation-strategy-{number:03d}",
            "record_kind": "generator_how",
            "source_plan": source_plan,
            "parent_strategy_id": previous["strategy_id"],
            "implementation": {
                "approach": approach,
                "change_set": change_set,
                "interfaces": interfaces,
                "sequence": sequence,
                "test_commands": test_commands,
                "rollback": rollback,
                "risks": risks,
            },
        },
    )


def validate_strategy_chain(
    project_root: str | Path,
    *,
    approved_plan: str | None = None,
    require_generator: bool = False,
) -> list[dict[str, Any]]:
    """校验追加式链，返回按编号排列的不可变快照。"""

    root = Path(project_root).resolve()
    paths = _strategy_files(root)
    if not paths:
        raise _error("缺少 Implementation Strategy 记录")
    records: list[dict[str, Any]] = []
    for index, path in enumerate(paths, 1):
        record = _load_record(path)
        validate_strategy_record(record)
        expected_id = f"implementation-strategy-{index:03d}"
        if record["strategy_id"] != expected_id:
            raise _error(f"记录编号不连续：期望 {expected_id}")
        if path.name != f"{expected_id}.yaml":
            raise _error(f"文件名与记录编号不一致：{path.name}")
        records.append(record)
    if records[0]["record_kind"] != "planner_what_why":
        raise _error("第一条记录必须是 Planner WHAT/WHY")
    if sum(record["record_kind"] == "planner_what_why" for record in records) != 1:
        raise _error("Planner WHAT/WHY 只能出现一次")
    source_plan = records[0]["source_plan"]
    if approved_plan is not None and source_plan != approved_plan:
        raise _error("策略链 source_plan 与 approved_plan 不一致")
    for previous, current in zip(records, records[1:]):
        if current["record_kind"] != "generator_how":
            raise _error("首条之后只能追加 Generator HOW")
        if current["source_plan"] != source_plan:
            raise _error("所有策略记录必须引用同一个 source_plan")
        if current["parent_strategy_id"] != previous["strategy_id"]:
            raise _error("Generator HOW 的 parent_strategy_id 链接无效")
    if require_generator and not any(record["record_kind"] == "generator_how" for record in records):
        raise _error("当前阶段要求至少一条 Generator HOW 记录")
    return records


__all__ = [
    "append_generator_strategy_record",
    "create_planner_strategy_record",
    "validate_strategy_chain",
    "validate_strategy_record",
]
