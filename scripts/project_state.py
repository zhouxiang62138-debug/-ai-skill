"""AI Development Team 项目状态读取、校验与兼容性工具。

仅处理 project.yaml 使用的受限 YAML 子集，不依赖第三方包。工具默认只读；
写入函数使用同目录临时文件和原子替换，调用方必须显式调用。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO_ROOT / "config" / "schemas"
SUPPORTED_SCHEMA_VERSIONS = (3, 4)


class ProjectStateError(ValueError):
    """项目状态无法安全读取或校验。"""


@dataclass(frozen=True)
class _Token:
    indent: int
    text: str
    line: int


def _strip_comment(text: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None and (index == 0 or text[index - 1].isspace()):
            return text[:index].rstrip()
    return text.rstrip()


def _tokenize_yaml(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for line_number, raw_line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ProjectStateError(f"第 {line_number} 行使用了 Tab 缩进")
        content = _strip_comment(raw_line)
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        if indent % 2:
            raise ProjectStateError(f"第 {line_number} 行缩进必须是 2 的倍数")
        tokens.append(_Token(indent, content.strip(), line_number))
    return tokens


def _parse_scalar(value: str, line: int) -> Any:
    if value in ("null", "~"):
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value.startswith(("&", "*", "!")):
        raise ProjectStateError(f"第 {line} 行不允许 YAML 锚点、别名或标签")
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProjectStateError(f"第 {line} 行的行内结构不是合法 JSON") from exc
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProjectStateError(f"第 {line} 行的双引号字符串无效") from exc
    if value.startswith("'"):
        if not value.endswith("'") or len(value) < 2:
            raise ProjectStateError(f"第 {line} 行的单引号字符串无效")
        return value[1:-1].replace("''", "'")
    if re.fullmatch(r"-?(0|[1-9]\d*)", value):
        return int(value)
    return value


def _split_mapping(text: str, line: int) -> tuple[str, str]:
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?", text)
    if not match:
        raise ProjectStateError(f"第 {line} 行不是受支持的 key: value 结构")
    return match.group(1), match.group(2) or ""


def _parse_block(tokens: list[_Token], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(tokens) or tokens[index].indent != indent:
        raise ProjectStateError("YAML 块缩进不一致")
    is_list = tokens[index].text.startswith("-")
    result: Any = [] if is_list else {}

    while index < len(tokens):
        token = tokens[index]
        if token.indent < indent:
            break
        if token.indent > indent:
            raise ProjectStateError(f"第 {token.line} 行出现未关联的缩进块")

        if is_list:
            if not token.text.startswith("-"):
                raise ProjectStateError(f"第 {token.line} 行混合了列表和映射")
            item_text = token.text[1:].strip()
            index += 1
            if not item_text:
                if index >= len(tokens) or tokens[index].indent <= indent:
                    result.append(None)
                else:
                    item, index = _parse_block(tokens, index, tokens[index].indent)
                    result.append(item)
            elif re.match(r"[A-Za-z_][A-Za-z0-9_-]*:", item_text):
                key, raw_value = _split_mapping(item_text, token.line)
                item_map: dict[str, Any] = {}
                item_map[key] = _parse_scalar(raw_value, token.line) if raw_value else None
                if index < len(tokens) and tokens[index].indent > indent:
                    continuation, index = _parse_block(tokens, index, tokens[index].indent)
                    if not isinstance(continuation, dict):
                        raise ProjectStateError(f"第 {token.line} 行的列表映射续块无效")
                    if set(item_map) & set(continuation):
                        raise ProjectStateError(f"第 {token.line} 行的列表映射包含重复键")
                    item_map.update(continuation)
                result.append(item_map)
            else:
                result.append(_parse_scalar(item_text, token.line))
            continue

        if token.text.startswith("-"):
            raise ProjectStateError(f"第 {token.line} 行混合了映射和列表")
        key, raw_value = _split_mapping(token.text, token.line)
        if key in result:
            raise ProjectStateError(f"第 {token.line} 行包含重复键 {key}")
        index += 1
        if raw_value:
            result[key] = _parse_scalar(raw_value, token.line)
        elif index < len(tokens) and tokens[index].indent > indent:
            result[key], index = _parse_block(tokens, index, tokens[index].indent)
        else:
            result[key] = None
    return result, index


def parse_project_yaml(text: str) -> dict[str, Any]:
    """解析 project.yaml 使用的受限、安全 YAML 子集。"""

    tokens = _tokenize_yaml(text)
    if not tokens:
        raise ProjectStateError("project.yaml 为空")
    if tokens[0].indent != 0:
        raise ProjectStateError("project.yaml 顶层必须从零缩进开始")
    value, index = _parse_block(tokens, 0, 0)
    if index != len(tokens) or not isinstance(value, dict):
        raise ProjectStateError("project.yaml 顶层必须是映射")
    return value


def load_project_state(path: str | Path) -> dict[str, Any]:
    project_path = Path(path)
    if project_path.name != "project.yaml":
        raise ProjectStateError("项目状态文件必须命名为 project.yaml")
    return parse_project_yaml(project_path.read_text(encoding="utf-8"))


def _quote_string(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./<>:-]+", value) and value not in {
        "null",
        "true",
        "false",
        "~",
    }:
        return value
    return json.dumps(value, ensure_ascii=False)


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _quote_string(value)
    if value == []:
        return "[]"
    if value == {}:
        return "{}"
    raise ProjectStateError(f"不支持的标量类型：{type(value).__name__}")


def _dump_block(value: Any, indent: int, output: list[str]) -> None:
    prefix = " " * indent
    if isinstance(value, dict):
        for key, item in value.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key):
                raise ProjectStateError(f"无法写入不安全的键名：{key}")
            if isinstance(item, (dict, list)) and item:
                output.append(f"{prefix}{key}:")
                _dump_block(item, indent + 2, output)
            else:
                output.append(f"{prefix}{key}: {_dump_scalar(item)}")
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)) and item:
                output.append(f"{prefix}-")
                _dump_block(item, indent + 2, output)
            else:
                output.append(f"{prefix}- {_dump_scalar(item)}")
        return
    raise ProjectStateError(f"无法写入块类型：{type(value).__name__}")


def serialize_project_state(state: dict[str, Any]) -> str:
    output: list[str] = []
    _dump_block(state, 0, output)
    return "\n".join(output) + "\n"


def write_project_state_atomic(path: str | Path, state: dict[str, Any]) -> None:
    """校验通过后原子写入；不会在校验失败时破坏原状态文件。"""

    errors = validate_project_state(state)
    if errors:
        raise ProjectStateError("拒绝写入无效状态：" + "; ".join(errors))
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=".project-state-", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as temporary:
            temporary.write(serialize_project_state(state))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _validate_schema_node(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_matches_type(value, item) for item in expected_types):
            return [f"{path} 类型错误，期望 {expected_types}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} 必须等于 {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} 的值 {value!r} 不在允许枚举中")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path} 不能为空")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path} 不能小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path} 不能大于 {schema['maximum']}")
    if isinstance(value, dict):
        for required_key in schema.get("required", []):
            if required_key not in value:
                errors.append(f"{path}.{required_key} 缺失")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors.extend(_validate_schema_node(item, properties[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{key} 是未知字段")
    return errors


def _load_schema(version: int) -> dict[str, Any]:
    schema_path = SCHEMA_DIR / f"project_v{version}.schema.json"
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _require(state: dict[str, Any], field: str, errors: list[str]) -> None:
    if state.get(field) in (None, "", []):
        errors.append(f"$.{field} 在当前状态下不能为空")


def _validate_semantics(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = state.get("schema_version")
    status = state.get("status")

    if status == "INTAKE":
        if state.get("active_module") != "first_ask_intake":
            errors.append("$.active_module 在 INTAKE 中必须是 first_ask_intake")
        if state.get("next_role") is not None:
            errors.append("$.next_role 在 INTAKE 中必须是 null")

    if state.get("current_iteration") == 5 and status in {"IMPLEMENTING", "EVALUATING"}:
        errors.append("current_iteration 达到 5 后不得继续自动实现或评估")

    if version == 3 and status in {"APPROVED_FOR_IMPLEMENTATION", "PLANNING_COMPLETE"}:
        for field in (
            "active_requirements",
            "approved_proposal",
            "product_approval_record",
            "active_plan",
        ):
            _require(state, field, errors)
        if state.get("next_role") != "generator":
            errors.append("$.next_role 在 v3 实施批准状态中必须是 generator")

    if version == 4:
        if status in {"DESIGN_REVIEW", "PRODUCT_REVIEW", "PLANNING_COMPLETE"}:
            errors.append("v4 新项目不得写入旧状态别名")
        if status == "WAITING_FOR_PRODUCT_REVIEW" and state.get("active_plan") is not None:
            errors.append("产品方案确认前 active_plan 必须为 null")
        if status == "WAITING_FOR_PLAN_REVIEW":
            for field in (
                "approved_proposal",
                "product_approval_record",
                "active_product_spec",
                "active_plan",
            ):
                _require(state, field, errors)
            if state.get("product_spec_status") != "finalized":
                errors.append("等待 Plan 审核时 product_spec_status 必须是 finalized")
            if state.get("plan_status") != "waiting_user_review":
                errors.append("等待 Plan 审核时 plan_status 必须是 waiting_user_review")
            if state.get("plan_approval_status") != "waiting_explicit_confirmation":
                errors.append(
                    "等待 Plan 审核时 plan_approval_status 必须是 waiting_explicit_confirmation"
                )
            if state.get("approved_plan") is not None:
                errors.append("Plan 明确批准前 approved_plan 必须为 null")
            if state.get("next_role") != "planner":
                errors.append("等待 Plan 审核时 next_role 必须是 planner")
        if status == "APPROVED_FOR_IMPLEMENTATION":
            for field in (
                "active_requirements",
                "approved_proposal",
                "product_approval_record",
                "active_product_spec",
                "active_plan",
                "approved_plan",
                "plan_approval_record",
            ):
                _require(state, field, errors)
            if state.get("plan_status") != "approved":
                errors.append("进入实施批准状态前 plan_status 必须是 approved")
            if state.get("plan_approval_status") != "approved":
                errors.append("进入实施批准状态前 plan_approval_status 必须是 approved")
            if state.get("approved_plan") != state.get("active_plan"):
                errors.append("approved_plan 必须与 active_plan 指向同一正式计划")
            if state.get("next_role") != "generator":
                errors.append("进入实施批准状态后 next_role 必须是 generator")
    return errors


def validate_project_state(
    state: dict[str, Any], project_root: str | Path | None = None
) -> list[str]:
    version = state.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return [f"不支持 schema_version={version!r}，仅支持 3 和 4"]
    errors = _validate_schema_node(state, _load_schema(version), "$")
    errors.extend(_validate_semantics(state))
    if project_root is not None:
        root = Path(project_root).resolve()
        for field in (
            "active_requirements",
            "active_proposal",
            "approved_proposal",
            "product_approval_record",
            "active_product_spec",
            "active_plan",
            "approved_plan",
            "plan_approval_record",
            "design_selection_record",
            "design_skip_record",
            "exploration_feedback_record",
        ):
            reference = state.get(field)
            if not reference:
                continue
            candidate = (root / reference).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                errors.append(f"$.{field} 指向项目目录之外")
                continue
            if not candidate.exists():
                errors.append(f"$.{field} 指向不存在的文件：{reference}")
    return errors


V4_DEFAULTS: dict[str, Any] = {
    "product_spec_status": "not_started",
    "product_spec_version": 0,
    "active_product_spec": None,
    "plan_status": "not_started",
    "plan_version": 0,
    "approved_plan": None,
    "plan_approval_status": "not_requested",
    "plan_approval_record": None,
    "exploration_feedback_record": None,
}


def v3_compatibility_view(state: dict[str, Any]) -> dict[str, Any]:
    """为 v3 提供只读默认视图，不改变 schema_version 或原对象。"""

    if state.get("schema_version") != 3:
        return copy.deepcopy(state)
    view = copy.deepcopy(state)
    for key, value in V4_DEFAULTS.items():
        view.setdefault(key, copy.deepcopy(value))
    return view


def assess_v3_migration(state: dict[str, Any]) -> str:
    """判断 v3 是否可在后续阶段自动迁移；本函数不写文件。"""

    if state.get("schema_version") != 3:
        return "not_applicable"
    status = state.get("status")
    if status == "ARCHIVED":
        return "archived_do_not_migrate"
    if status in {
        "APPROVED_FOR_IMPLEMENTATION",
        "PLANNING_COMPLETE",
        "IMPLEMENTING",
        "EVALUATING",
        "ACCEPTED",
    }:
        return "manual_review_required"
    return "eligible_for_on_demand_migration"


def _run_cli() -> int:
    parser = argparse.ArgumentParser(description="校验 AI Development Team project.yaml")
    parser.add_argument("project_yaml", type=Path)
    parser.add_argument("--check-paths", action="store_true", help="检查活动工件路径")
    parser.add_argument(
        "--migration-assessment", action="store_true", help="输出 v3 迁移评估"
    )
    args = parser.parse_args()
    try:
        state = load_project_state(args.project_yaml)
        errors = validate_project_state(
            state, args.project_yaml.parent if args.check_paths else None
        )
    except (OSError, ProjectStateError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(f"PASS: schema v{state['schema_version']} project state is valid")
    if args.migration_assessment:
        print(f"MIGRATION: {assess_v3_migration(state)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_cli())
