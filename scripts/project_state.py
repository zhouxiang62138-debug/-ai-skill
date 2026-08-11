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
SUPPORTED_SCHEMA_VERSIONS = (3, 4, 5, 6, 7)


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


def write_project_state_atomic(
    path: str | Path,
    state: dict[str, Any],
) -> None:
    """校验通过后原子写入。

    一旦目标或候选状态为 v7，普通 writer 一律拒绝；Runtime CAS 与显式迁移/回滚
    只能通过本模块的私有适配器写入。因此遗留 writer 无法用公开参数绕过 revision
    和 Lease 直接覆盖。
    """

    errors = validate_project_state(state)
    if errors:
        raise ProjectStateError("拒绝写入无效状态：" + "; ".join(errors))
    target = Path(path).resolve()
    if target.is_file():
        current = load_project_state(target)
        if current.get("schema_version") == 7 or state.get("schema_version") == 7:
            raise ProjectStateError(
                "schema v7 状态必须通过 Runtime CAS 或显式迁移/回滚写入"
            )
    _write_project_state_atomic_unchecked(target, state)


def _write_runtime_project_state_atomic(path: str | Path, state: dict[str, Any]) -> None:
    """仅供 Runtime CAS 与迁移适配器使用的受控写入通道。"""

    target = Path(path).resolve()
    errors = validate_project_state(state)
    if errors:
        raise ProjectStateError("拒绝写入无效状态：" + "; ".join(errors))
    _write_project_state_atomic_unchecked(target, state)


def _write_project_state_atomic_unchecked(target: Path, state: dict[str, Any]) -> None:
    """执行原子替换；调用方必须先完成写入权限和状态校验。"""

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
        if os.name != "nt":
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
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
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path} 至少需要 {schema['minItems']} 项")
        if schema.get("uniqueItems") and len({repr(item) for item in value}) != len(value):
            errors.append(f"{path} 不允许重复项")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(
                    _validate_schema_node(item, schema["items"], f"{path}[{index}]")
                )
    return errors


def _load_schema(version: int) -> dict[str, Any]:
    schema_path = SCHEMA_DIR / f"project_v{version}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if version != 7:
        return schema
    # v7 是对完整 v6 业务契约的纯追加 Runtime 扩展。自定义验证器在这里
    # 确定性合并，避免复制两百余行后发生业务 Schema 漂移。
    base = json.loads(
        (SCHEMA_DIR / "project_v6.schema.json").read_text(encoding="utf-8")
    )
    base["$id"] = schema["$id"]
    base["title"] = schema["title"]
    base["properties"]["schema_version"] = {"const": 7}
    base["properties"]["runtime"] = schema["runtimeExtension"]
    base["required"] = [*base["required"], "runtime"]
    return base


def _require(state: dict[str, Any], field: str, errors: list[str]) -> None:
    if state.get(field) in (None, "", []):
        errors.append(f"$.{field} 在当前状态下不能为空")


def _validate_semantics(state: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = state.get("schema_version")
    status = state.get("status")
    design_preview_mode = state.get("design_preview_mode")
    if design_preview_mode not in {
        None,
        "not_started",
        "legacy_full",
        "direction_comparison",
        "selected_prototype",
    }:
        errors.append("design_preview_mode 无效")
    if version == 7 and design_preview_mode == "legacy_full":
        migration = state.get("schema_migration")
        if not (
            isinstance(migration, dict)
            and migration.get("from_version") in {3, 4, 5, 6}
            and migration.get("to_version") == 7
        ):
            errors.append(
                "schema_version=7 新项目不得使用 legacy_full；"
                "该模式只允许来自 v3-v6 到 v7 的迁移兼容路径"
            )

    if status == "INTAKE":
        if state.get("active_module") != "first_ask_intake":
            errors.append("$.active_module 在 INTAKE 中必须是 first_ask_intake")
        if state.get("next_role") is not None:
            errors.append("$.next_role 在 INTAKE 中必须是 null")

    if status == "REQUIREMENT_RESEARCH":
        if state.get("active_module") != "domain_research":
            errors.append("$.active_module 在 REQUIREMENT_RESEARCH 中必须是 domain_research")
        if state.get("next_role") is not None:
            errors.append("$.next_role 在 REQUIREMENT_RESEARCH 中必须是 null")
        if state.get("research_status") not in {"planned", "running"}:
            errors.append("REQUIREMENT_RESEARCH 要求 research_status 为 planned 或 running")
        if not state.get("active_research_round"):
            errors.append("REQUIREMENT_RESEARCH 必须绑定 active_research_round")
        if state.get("active_plan") is not None:
            errors.append("REQUIREMENT_RESEARCH 期间 active_plan 必须为 null")

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

    if version in {4, 5, 6, 7}:
        if status in {"DESIGN_REVIEW", "PRODUCT_REVIEW", "PLANNING_COMPLETE"}:
            errors.append("v4/v5/v6 新项目不得写入旧状态别名")
        if status == "REFERENCE_ANALYSIS":
            if not state.get("active_requirements"):
                errors.append("REFERENCE_ANALYSIS 必须先绑定 active_requirements")
            if state.get("requirements_status") != "sufficient_for_planning":
                errors.append("REFERENCE_ANALYSIS 要求需求已足以规划")
            if state.get("reference_status") not in {"provided", "ready"}:
                errors.append("REFERENCE_ANALYSIS 要求 reference_status 为 provided 或 ready")
            if state.get("reference_analysis_status") not in {"not_started", "running", "blocked"}:
                errors.append("REFERENCE_ANALYSIS 要求 reference_analysis_status 为 not_started、running 或 blocked")
            if state.get("active_module") != "reference_analysis":
                errors.append("REFERENCE_ANALYSIS 的 active_module 必须是 reference_analysis")
            if state.get("next_role") is not None:
                errors.append("REFERENCE_ANALYSIS 的 next_role 必须是 null")
            if state.get("active_plan") is not None:
                errors.append("REFERENCE_ANALYSIS 期间 active_plan 必须为 null")
        if status == "DESIGN_EXPLORATION":
            for field in ("active_requirements", "active_proposal"):
                _require(state, field, errors)
            if state.get("requirements_status") != "sufficient_for_planning":
                errors.append("进入设计探索前需求必须足以规划")
            if state.get("design_exploration_required") is not True:
                errors.append("DESIGN_EXPLORATION 要求 design_exploration_required=true")
            if not state.get("exploration_trigger_reasons"):
                errors.append("DESIGN_EXPLORATION 必须记录 exploration_trigger_reasons")
            if state.get("design_review_status") not in {
                "generating",
                "revision_requested",
            }:
                errors.append("DESIGN_EXPLORATION 的 design_review_status 无效")
            if state.get("active_plan") is not None:
                errors.append("设计探索期间 active_plan 必须为 null")
            if state.get("next_role") != "planner":
                errors.append("设计探索期间 next_role 必须是 planner")
            if design_preview_mode == "selected_prototype":
                _require(state, "selected_design_concept", errors)
                _require(state, "design_selection_record", errors)
        if status == "WAITING_FOR_DESIGN_REVIEW":
            _require(state, "active_design_preview_round", errors)
            if state.get("design_exploration_required") is not True:
                errors.append("等待设计审核时必须启用设计探索")
            expected_review_status = (
                "waiting_selected_prototype_confirmation"
                if design_preview_mode == "selected_prototype"
                else "waiting_user_selection"
            )
            if state.get("design_review_status") != expected_review_status:
                errors.append(
                    "WAITING_FOR_DESIGN_REVIEW 的 design_review_status 必须与 "
                    "design_preview_mode 对应"
                )
            if state.get("active_plan") is not None:
                errors.append("设计审核期间 active_plan 必须为 null")
            if state.get("next_role") != "planner":
                errors.append("等待设计审核时 next_role 必须是 planner")
            if not 1 <= state.get("exploration_generation_attempt", 0) <= 2:
                errors.append("等待设计审核时生成尝试次数必须在 1 到 2 之间")
            if state.get("design_feedback_status") not in {
                "waiting_user_feedback",
                "discussing",
                "ambiguous",
                "conflicting",
            }:
                errors.append("等待设计审核时 design_feedback_status 无效")
            if design_preview_mode == "selected_prototype":
                _require(state, "selected_design_concept", errors)
                _require(state, "design_selection_record", errors)
        if status == "PLANNING_REVISION" and state.get("design_review_status") == "direction_selected":
            _require(state, "selected_design_concept", errors)
            _require(state, "design_selection_record", errors)
            _require(state, "exploration_feedback_record", errors)
            if state.get("design_feedback_status") not in {
                "direction_selected",
                "modification_requested",
                "blend_selected",
                "prototype_confirmed",
            }:
                errors.append("设计方向已选择时 design_feedback_status 无效")
            if state.get("active_plan") is not None:
                errors.append("整合设计方向期间 active_plan 必须为 null")
            if state.get("next_role") != "planner":
                errors.append("整合设计方向期间 next_role 必须是 planner")
        if (
            status == "DESIGN_EXPLORATION"
            and state.get("design_review_status") == "revision_requested"
            and state.get("design_feedback_status") == "all_rejected"
        ):
            _require(state, "exploration_feedback_record", errors)
            if state.get("selected_design_concept") is not None:
                errors.append("全部方向被否定后 selected_design_concept 必须为 null")
        if (
            state.get("design_exploration_required") is False
            and state.get("design_review_status") == "skipped_by_user"
        ):
            _require(state, "design_skip_record", errors)
        if status == "WAITING_FOR_PRODUCT_REVIEW":
            if state.get("active_plan") is not None:
                errors.append("产品方案确认前 active_plan 必须为 null")
            if state.get("design_exploration_required") is True:
                _require(state, "selected_design_concept", errors)
                _require(state, "design_selection_record", errors)
                if state.get("design_review_status") != "integrated_into_proposal":
                    errors.append("产品审核前设计方向必须已整合进方案")
                if state.get("design_feedback_status") != "integrated_into_proposal":
                    errors.append("产品审核前设计反馈必须已整合进方案")
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
            if state.get("proposal_status") != "approved":
                errors.append("等待 Plan 审核时 proposal_status 必须是 approved")
            if state.get("active_proposal") != state.get("approved_proposal"):
                errors.append("等待 Plan 审核时 approved_proposal 必须与 active_proposal 一致")
            if state.get("user_approval_status") != "approved":
                errors.append("等待 Plan 审核前产品方案必须已明确批准")
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
            if state.get("product_spec_status") != "finalized":
                errors.append("进入实施批准状态前正式产品规格必须 finalized")
            if state.get("proposal_status") != "approved":
                errors.append("进入实施批准状态前产品方案必须 approved")
            if state.get("active_proposal") != state.get("approved_proposal"):
                errors.append("approved_proposal 必须与 active_proposal 一致")
            if state.get("plan_approval_status") != "approved":
                errors.append("进入实施批准状态前 plan_approval_status 必须是 approved")
            if state.get("approved_plan") != state.get("active_plan"):
                errors.append("approved_plan 必须与 active_plan 指向同一正式计划")
            if state.get("next_role") != "generator":
                errors.append("进入实施批准状态后 next_role 必须是 generator")
        if status in {
            "CHANGE_REQUESTED",
            "WAITING_FOR_CHANGE_APPROVAL",
            "RELEASE_READY",
        }:
            _require(state, "active_change_request", errors)
            if not isinstance(state.get("change_context"), dict):
                errors.append("活动 Change Request 状态必须声明 change_context")
            if status in {"CHANGE_REQUESTED", "WAITING_FOR_CHANGE_APPROVAL"}:
                if state.get("next_role") != "planner":
                    errors.append(f"{status} 的 next_role 必须是 planner")
            if status == "RELEASE_READY" and state.get("next_role") != "evaluator":
                errors.append("RELEASE_READY 的 next_role 必须是 evaluator")
        if state.get("active_change_request") is not None:
            if not isinstance(state.get("change_context"), dict):
                errors.append("active_change_request 必须配套 change_context")
            else:
                iteration = state["change_context"].get("evaluation_iteration")
                if iteration != state.get("current_iteration"):
                    errors.append(
                        "Change Request evaluation_iteration 必须与 current_iteration 一致"
                    )
        elif state.get("change_context") is not None:
            errors.append("没有 active_change_request 时 change_context 必须是 null")
    if version in {5, 6, 7} and state.get("project_type") == "skill_maintenance":
        targets = state.get("targets")
        if not isinstance(targets, dict):
            errors.append("skill_maintenance 必须声明 targets")
        else:
            expected = {
                "working_repository": "read_write",
                "installed_repository": "read_only_until_final_sync",
            }
            normalized: list[Path] = []
            for name, access in expected.items():
                target = targets.get(name)
                if not isinstance(target, dict) or target.get("access") != access:
                    errors.append(f"targets.{name} 必须声明 access={access}")
                    continue
                raw_path = target.get("path")
                if not isinstance(raw_path, str) or not raw_path:
                    errors.append(f"targets.{name}.path 必须是非空绝对路径")
                    continue
                candidate = Path(raw_path)
                if not candidate.is_absolute() or ".." in candidate.parts:
                    errors.append(f"targets.{name}.path 必须是无路径穿越的绝对路径")
                    continue
                normalized.append(candidate.resolve())
            if len(normalized) == 2:
                working, installed = normalized
                if working == installed:
                    errors.append("工作副本和安装副本不得指向同一目录")
                else:
                    try:
                        installed.relative_to(working)
                        errors.append("安装副本不得位于工作副本内部")
                    except ValueError:
                        pass
                    try:
                        working.relative_to(installed)
                        errors.append("工作副本不得位于安装副本内部")
                    except ValueError:
                        pass
        sync = state.get("sync")
        if not isinstance(sync, dict) or not isinstance(sync.get("exclusions"), list):
            errors.append("skill_maintenance 必须声明 sync.exclusions")
    if version in {6, 7}:
        if not isinstance(state.get("iteration_sequence"), int) or state["iteration_sequence"] < 1:
            errors.append("v6 iteration_sequence 必须是正整数")
        if not isinstance(state.get("automatic_retry_allowed"), bool):
            errors.append("v6 automatic_retry_allowed 必须是布尔值")
        if state.get("current_iteration") == 5 and state.get("automatic_retry_allowed") is not False:
            errors.append("达到最大迭代后 automatic_retry_allowed 必须为 false")
        if state.get("automatic_retry_allowed") is False and status == "IMPLEMENTING":
            errors.append("禁止自动重试时不得进入 IMPLEMENTING")
        if state.get("escalation_record") is not None and state.get("automatic_retry_allowed") is not False:
            errors.append("存在升级记录时必须停止自动重试")
    return errors


def validate_project_state(
    state: dict[str, Any], project_root: str | Path | None = None
) -> list[str]:
    version = state.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        return [f"不支持 schema_version={version!r}，仅支持 3、4、5、6 和 7"]
    errors = _validate_schema_node(state, _load_schema(version), "$")
    errors.extend(_validate_semantics(state))
    if project_root is not None:
        root = Path(project_root).resolve()
        if state.get("schema_version") in {5, 6, 7} and state.get("project_type") == "skill_maintenance":
            for name, target in (state.get("targets") or {}).items():
                if not isinstance(target, dict) or not isinstance(target.get("path"), str):
                    continue
                resolved_target = Path(target["path"]).resolve()
                try:
                    resolved_target.relative_to(root)
                    errors.append(f"$.targets.{name} 不得位于项目目录内部")
                except ValueError:
                    pass
                try:
                    root.relative_to(resolved_target)
                    errors.append(f"项目目录不得位于 $.targets.{name} 内部")
                except ValueError:
                    pass
        for field in (
            "active_requirements",
            "intent_analysis_ref",
            "research_requirement_ref",
            "active_research_round",
            "coverage_map_ref",
            "gap_analysis_ref",
            "question_set_ref",
            "sufficiency_evaluation_ref",
            "opportunity_map_ref",
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
            "active_design_preview_round",
            "exploration_error_record",
            "approval_revocation_record",
            "change_request_record",
            "change_impact_analysis",
            "change_approval_record",
            "change_baseline",
            "current_release",
            "last_release_rollback",
            "last_evaluation",
            "last_issue_package",
            "last_generator_response",
            "evidence_manifest",
            "decision_summary_record",
            "schema_migration_record",
            "active_reference_synthesis",
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
            if (
                field == "active_design_preview_round"
                and state.get("status") == "DESIGN_EXPLORATION"
                and state.get("design_review_status") in {"generating", "revision_requested"}
            ):
                # 生成态先提交“本轮要生成到哪里”，随后才创建目录和工件。
                # 完成态仍由 exploration.finalize_preview_round 按当前模式校验：
                # 方向比较是三案，选中原型是单一 selected_concept，旧项目走兼容规则。
                continue
            if not candidate.exists():
                errors.append(f"$.{field} 指向不存在的文件：{reference}")
        selected = state.get("selected_design_concept")
        if isinstance(selected, dict):
            for index, reference in enumerate(selected.get("concept_refs") or []):
                candidate = (root / reference).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    errors.append(
                        f"$.selected_design_concept.concept_refs[{index}] "
                        "指向项目目录之外"
                    )
                    continue
                if not candidate.is_dir():
                    errors.append(
                        f"$.selected_design_concept.concept_refs[{index}] "
                        f"指向不存在的概念目录：{reference}"
                    )
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
    "exploration_trigger_reasons": [],
    "exploration_generation_attempt": 0,
    "exploration_error_record": None,
    "design_feedback_status": "not_started",
    "design_feedback_round": 0,
    "design_preview_mode": "legacy_full",
    "approval_revocation_record": None,
    "change_request_record": None,
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
