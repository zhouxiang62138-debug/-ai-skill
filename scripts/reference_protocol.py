"""Reference Analysis Protocol v1 的确定性结构和来源链校验。

本模块只验证协议记录，不抓取来源、不生成分析结果，也不写入项目文件。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from scripts.project_state import parse_project_yaml
except ModuleNotFoundError:  # 直接从 scripts 目录运行时的兼容导入
    from project_state import parse_project_yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config" / "reference_analysis.yaml"
DOMAINS = (
    "product",
    "information_architecture",
    "navigation",
    "interaction",
    "layout",
    "visual_style",
    "components",
    "design_tokens",
    "motion",
    "content_style",
    "brand",
    "technical_architecture",
)


class ReferenceProtocolError(ValueError):
    """Reference Analysis 记录不符合 v1 协议。"""


def load_reference_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    """从配置读取枚举和路径策略，避免把业务枚举散落在 Python 中。"""

    value = parse_project_yaml(Path(path).read_text(encoding="utf-8"))
    if value.get("version") != 1 or value.get("module") != "reference_analysis":
        raise ReferenceProtocolError("REFERENCE_CONFIG_VERSION_INVALID")
    return value


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _add(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _require(record: Mapping[str, Any], key: str, errors: list[str], path: str = "$") -> Any:
    if key not in record or record[key] in (None, ""):
        _add(errors, f"{path}.{key}", "缺少必填值")
        return None
    return record[key]


def _enum(value: Any, allowed: Iterable[Any], errors: list[str], path: str) -> None:
    values = tuple(allowed)
    if value not in values:
        _add(errors, path, f"值 {value!r} 不在配置枚举中")


def _pattern(value: Any, pattern: str, errors: list[str], path: str) -> None:
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        _add(errors, path, f"不符合 {pattern}")


def _cfg_values(config: Mapping[str, Any], section: str, key: str) -> list[Any]:
    value = config.get(section, {}).get(key, [])
    return list(value) if isinstance(value, list) else []


def _id_pattern(config: Mapping[str, Any], kind: str) -> str:
    return str(config.get("id_patterns", {}).get(kind, r"^$"))


def _safe_project_path(value: Any, errors: list[str], path: str, roots: tuple[str, ...]) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        _add(errors, path, "必须是非空项目相对路径")
        return
    normalized = value.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or ".." in candidate.parts:
        _add(errors, path, "不得是绝对路径或包含 ..")
        return
    if not any(normalized == root or normalized.startswith(root + "/") for root in roots):
        _add(errors, path, f"必须位于 {roots} 下")


def _validate_context(value: Any, errors: list[str], path: str = "$.context") -> None:
    if not _is_mapping(value):
        _add(errors, path, "必须是对象")
        return
    _enum(value.get("type"), ("project", "new_project", "change_request"), errors, f"{path}.type")
    project_id = value.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        _add(errors, f"{path}.project_id", "必须是非空字符串")
    change_request_id = value.get("change_request_id")
    if value.get("type") == "change_request":
        _pattern(change_request_id, r"^CR-[0-9]{4}$", errors, f"{path}.change_request_id")
    elif change_request_id not in (None, ""):
        _add(errors, f"{path}.change_request_id", "project/new_project 上下文不得绑定 Change Request")


def _validate_scope_fields(
    scope: Any, config: Mapping[str, Any], errors: list[str], path: str = "$.requested_scope"
) -> None:
    if not _is_mapping(scope):
        _add(errors, path, "必须是对象，不能使用布尔值代替三态范围")
        return
    domains = list(config.get("analysis_domains", []))
    for domain in domains:
        value = scope.get(domain)
        if value is None:
            _add(errors, f"{path}.{domain}", "必须明确为 include、exclude 或 unspecified")
        else:
            _enum(value, config.get("scope_states", []), errors, f"{path}.{domain}")
    extras = set(scope) - set(domains)
    for key in sorted(extras):
        _add(errors, f"{path}.{key}", "不是 v1 范围维度")


def _validate_scope_lists(record: Mapping[str, Any], config: Mapping[str, Any], errors: list[str], path: str = "$") -> None:
    domains = list(config.get("analysis_domains", []))
    for key in ("explicit_inclusions", "explicit_exclusions"):
        value = record.get(key)
        if not isinstance(value, list):
            _add(errors, f"{path}.{key}", "必须是数组")
            continue
        if len(set(value)) != len(value):
            _add(errors, f"{path}.{key}", "不得重复")
        for index, item in enumerate(value):
            _enum(item, domains, errors, f"{path}.{key}[{index}]")
    inclusions = set(record.get("explicit_inclusions") or [])
    exclusions = set(record.get("explicit_exclusions") or [])
    if inclusions & exclusions:
        _add(errors, f"{path}.explicit_inclusions", "不得与 explicit_exclusions 交叉")


def _validate_trust(record: Mapping[str, Any], errors: list[str], path: str = "$") -> None:
    if record.get("trust_level") != "untrusted":
        _add(errors, f"{path}.trust_level", "Reference 输入必须是 untrusted")
    for key in ("instruction_authority", "workflow_authority", "runtime_authority"):
        if record.get(key) is True:
            _add(errors, f"{path}.{key}", "不可信引用不得表达执行权限")


def validate_reference_source(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """验证来源登记；这里只登记来源，不读取或分析来源内容。"""

    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    _enum(record.get("source_type"), config.get("source_types", {}).get("active", []), errors, "$.source_type")
    source = record.get("source")
    if not _is_mapping(source):
        _add(errors, "$.source", "必须是对象")
        source = {}
    for key in ("content", "html", "image_data", "raw_text"):
        if key in source:
            _add(errors, f"$.source.{key}", "v1 不允许内嵌原始内容")
    locator_requirements = config.get("source_locator_requirements", {}).get(record.get("source_type"), [])
    if record.get("source_type") == "web_page" and not isinstance(source.get("uri"), str):
        _add(errors, "$.source.uri", "web_page 需要 URI")
    if record.get("source_type") == "web_page" and isinstance(source.get("uri"), str) and not re.match(r"^https?://", source["uri"]):
        _add(errors, "$.source.uri", "web_page URI 必须使用 http 或 https")
    if record.get("source_type") == "image" and not isinstance(source.get("uri"), str) and not isinstance(source.get("artifact_ref"), str):
        _add(errors, "$.source", "image 需要 uri 或 artifact_ref")
    if record.get("source_type") == "text_description" and not isinstance(source.get("text_ref"), str) and not isinstance(source.get("identifier"), str):
        _add(errors, "$.source", "text_description 需要 text_ref 或 identifier")
    if not locator_requirements and record.get("source_type") in config.get("source_types", {}).get("active", []):
        _add(errors, "$.source_type", "配置缺少来源定位规则")
    _safe_project_path(record.get("scope_ref"), errors, "$.scope_ref", ("memory", "change_requests"))
    _validate_scope_fields(record.get("requested_scope"), config, errors)
    _validate_scope_lists(record, config, errors)
    _enum(record.get("status"), config.get("source_statuses", []), errors, "$.status")
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    origin = record.get("source_origin")
    if not _is_mapping(origin):
        _add(errors, "$.source_origin", "必须是对象")
    else:
        _enum(origin.get("type"), config.get("source_origins", []), errors, "$.source_origin.type")
    _validate_trust(record, errors)
    _validate_context(record.get("context"), errors)
    return errors


def validate_reference_scope(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("scope_id"), _id_pattern(config, "scope"), errors, "$.scope_id")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    _validate_scope_fields(record.get("requested_scope"), config, errors)
    _validate_scope_lists(record, config, errors)
    source_refs = record.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        _add(errors, "$.source_refs", "至少需要一个来源 ID")
    else:
        for index, item in enumerate(source_refs):
            _pattern(item, _id_pattern(config, "reference"), errors, f"$.source_refs[{index}]")
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    _validate_context(record.get("context"), errors)
    return errors


def validate_reference_evidence(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("evidence_id"), _id_pattern(config, "evidence"), errors, "$.evidence_id")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    _enum(record.get("evidence_type"), config.get("evidence_types", []), errors, "$.evidence_type")
    _safe_project_path(record.get("artifact_ref"), errors, "$.artifact_ref", ("memory", "artifacts", "change_requests"))
    integrity = record.get("integrity")
    if not _is_mapping(integrity):
        _add(errors, "$.integrity", "必须包含 sha256 完整性信息")
    else:
        if integrity.get("algorithm") != "sha256":
            _add(errors, "$.integrity.algorithm", "必须是 sha256")
        _pattern(integrity.get("sha256"), r"^[a-fA-F0-9]{64}$", errors, "$.integrity.sha256")
    viewport = record.get("viewport")
    if viewport is not None:
        if not _is_mapping(viewport) or not isinstance(viewport.get("width"), int) or not isinstance(viewport.get("height"), int) or viewport["width"] < 1 or viewport["height"] < 1:
            _add(errors, "$.viewport", "宽高必须是正整数")
    if record.get("acquisition_id") is not None:
        _pattern(record.get("acquisition_id"), r"^ACQ-[0-9]{6}$", errors, "$.acquisition_id")
    if record.get("request_fingerprint") is not None:
        _pattern(record.get("request_fingerprint"), r"^[a-fA-F0-9]{64}$", errors, "$.request_fingerprint")
    if record.get("acquisition_status") is not None:
        _enum(record.get("acquisition_status"), config.get("acquisition", {}).get("lifecycle", []), errors, "$.acquisition_status")
    if record.get("provider_version") is not None and (not isinstance(record.get("provider_version"), int) or record["provider_version"] < 1):
        _add(errors, "$.provider_version", "provider_version 必须是正整数")
    if record.get("snapshot_version") is not None and (not isinstance(record.get("snapshot_version"), int) or record["snapshot_version"] < 1):
        _add(errors, "$.snapshot_version", "snapshot_version 必须是正整数")
    _validate_trust(record, errors)
    return errors


def validate_reference_attachment_binding(
    record: Mapping[str, Any], config: Mapping[str, Any] | None = None
) -> list[str]:
    """校验宿主附件到项目 Evidence 的绑定摘要，不读取宿主原始路径。"""

    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    if record.get("binding_type") != "host_attachment_reference_evidence":
        _add(errors, "$.binding_type", "绑定类型无效")
    _pattern(record.get("binding_id"), r"^REFBND-[0-9]{3}$", errors, "$.binding_id")
    _pattern(record.get("idempotency_key"), r"^[a-fA-F0-9]{64}$", errors, "$.idempotency_key")
    _pattern(record.get("attachment_id"), r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", errors, "$.attachment_id")
    _pattern(record.get("project_id"), r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", errors, "$.project_id")
    _pattern(record.get("project_root_hash"), r"^[a-fA-F0-9]{64}$", errors, "$.project_root_hash")
    _pattern(record.get("reference_id"), _id_pattern(config or load_reference_config(), "reference"), errors, "$.reference_id")
    _pattern(record.get("evidence_id"), _id_pattern(config or load_reference_config(), "evidence"), errors, "$.evidence_id")
    _safe_project_path(record.get("artifact_ref"), errors, "$.artifact_ref", ("artifacts",))
    _safe_project_path(record.get("evidence_ref"), errors, "$.evidence_ref", ("artifacts",))
    _pattern(record.get("sha256"), r"^[a-fA-F0-9]{64}$", errors, "$.sha256")
    if not isinstance(record.get("size_bytes"), int) or record["size_bytes"] < 0:
        _add(errors, "$.size_bytes", "必须是非负整数")
    if not isinstance(record.get("media_type"), str) or re.fullmatch(
        r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$", record.get("media_type", "").casefold()
    ) is None:
        _add(errors, "$.media_type", "媒体类型无效")
    _validate_trust(record, errors)
    return errors


def validate_acquisition_manifest(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """验证 RA7-B Manifest；只校验摘要，不读取原始采集内容。"""

    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    if record.get("manifest_type") != "acquisition_manifest":
        _add(errors, "$.manifest_type", "必须是 acquisition_manifest")
    _pattern(record.get("acquisition_id"), r"^ACQ-[0-9]{6}$", errors, "$.acquisition_id")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    if not isinstance(record.get("source_type"), str) or not record["source_type"]:
        _add(errors, "$.source_type", "source_type 必须是非空字符串")
    _pattern(record.get("request_fingerprint"), r"^[a-fA-F0-9]{64}$", errors, "$.request_fingerprint")
    if not isinstance(record.get("provider_id"), str) or not record["provider_id"]:
        _add(errors, "$.provider_id", "provider_id 必须是非空字符串")
    for key in ("provider_version", "snapshot_version", "attempt"):
        if not isinstance(record.get(key), int) or isinstance(record.get(key), bool) or record[key] < 1:
            _add(errors, f"$.{key}", f"{key} 必须是正整数")
    _enum(record.get("status"), config.get("acquisition", {}).get("lifecycle", []), errors, "$.status")
    for key in ("artifact_refs", "evidence_refs", "history"):
        if not isinstance(record.get(key), list):
            _add(errors, f"$.{key}", "必须是数组")
    if record.get("refresh_of") is not None:
        _pattern(record.get("refresh_of"), r"^ACQ-[0-9]{6}$", errors, "$.refresh_of")
    for key in ("created_at", "updated_at"):
        if not isinstance(record.get(key), str) or not record[key]:
            _add(errors, f"$.{key}", "必须是非空时间字符串")
    _validate_trust(record, errors)
    return errors


def validate_perception_run(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """校验多模态感知运行摘要，不读取或执行模型输出内容。"""

    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("run_id"), r"^PER-[0-9]{6}$", errors, "$.run_id")
    if not isinstance(record.get("provider_id"), str) or not record["provider_id"]:
        _add(errors, "$.provider_id", "必须是非空字符串")
    if not isinstance(record.get("provider_version"), int) or isinstance(record.get("provider_version"), bool) or record["provider_version"] < 1:
        _add(errors, "$.provider_version", "必须是正整数")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    hashes = record.get("input_evidence_hashes")
    if not isinstance(hashes, list) or not hashes:
        _add(errors, "$.input_evidence_hashes", "至少需要一个证据哈希")
    else:
        for index, item in enumerate(hashes):
            _pattern(item, r"^[a-fA-F0-9]{64}$", errors, f"$.input_evidence_hashes[{index}]")
    domains = record.get("requested_domains")
    if not isinstance(domains, list) or not domains:
        _add(errors, "$.requested_domains", "至少需要一个请求域")
    else:
        for index, item in enumerate(domains):
            _enum(item, config.get("analysis_domains", []), errors, f"$.requested_domains[{index}]")
    _enum(record.get("status"), ("UNAVAILABLE", "SUCCEEDED", "FAILED", "BLOCKED"), errors, "$.status")
    if record.get("result_hash") is not None:
        _pattern(record.get("result_hash"), r"^[a-fA-F0-9]{64}$", errors, "$.result_hash")
    limitations = record.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        _add(errors, "$.limitations", "必须是字符串数组")
    if not isinstance(record.get("model_identity"), str) or not record["model_identity"]:
        _add(errors, "$.model_identity", "必须是非空字符串")
    if record.get("failure_code") is not None and (
        not isinstance(record.get("failure_code"), str) or not record["failure_code"]
    ):
        _add(errors, "$.failure_code", "必须是非空字符串或 null")
    if not isinstance(record.get("transport"), str) or not record["transport"]:
        _add(errors, "$.transport", "必须是非空字符串")
    if record.get("sdk_version") is not None and (
        not isinstance(record.get("sdk_version"), str) or not record["sdk_version"]
    ):
        _add(errors, "$.sdk_version", "必须是非空字符串或 null")
    _validate_trust(record, errors)
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    return errors


def validate_browser_capture(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """校验 Browser Capture 摘要，页面正文只作为不可信 artifact 引用保存。"""

    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("capture_id"), r"^CAP-[a-f0-9]{16}-v[0-9]+$", errors, "$.capture_id")
    _pattern(record.get("reference_id"), _id_pattern(config or load_reference_config(), "reference"), errors, "$.reference_id")
    canonical_url = record.get("canonical_url")
    if not isinstance(canonical_url, str) or not canonical_url.startswith("https://"):
        _add(errors, "$.canonical_url", "必须是 HTTPS URL")
    redirects = record.get("redirect_chain")
    if not isinstance(redirects, list) or not redirects or not all(isinstance(item, str) and item.startswith("https://") for item in redirects):
        _add(errors, "$.redirect_chain", "必须是非空 HTTPS URL 数组")
    viewport = record.get("viewport")
    if not _is_mapping(viewport) or not isinstance(viewport.get("width"), int) or not isinstance(viewport.get("height"), int) or viewport["width"] < 1 or viewport["height"] < 1:
        _add(errors, "$.viewport", "必须包含正整数宽高")
    if not _is_mapping(record.get("browser_identity")):
        _add(errors, "$.browser_identity", "必须是对象")
    if not isinstance(record.get("captured_at"), str) or not record["captured_at"]:
        _add(errors, "$.captured_at", "必须是非空时间字符串")
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) < 3:
        _add(errors, "$.artifacts", "至少需要 DOM、Computed Style 和 Screenshot 三类 artifact")
    else:
        for index, artifact in enumerate(artifacts):
            path = f"$.artifacts[{index}]"
            if not _is_mapping(artifact):
                _add(errors, path, "必须是对象")
                continue
            _safe_project_path(artifact.get("artifact_ref"), errors, f"{path}.artifact_ref", ("artifacts",))
            _pattern(artifact.get("sha256"), r"^[a-fA-F0-9]{64}$", errors, f"{path}.sha256")
            if not isinstance(artifact.get("size_bytes"), int) or isinstance(artifact.get("size_bytes"), bool) or artifact["size_bytes"] < 1:
                _add(errors, f"{path}.size_bytes", "必须是正整数")
            if not isinstance(artifact.get("media_type"), str) or not artifact["media_type"]:
                _add(errors, f"{path}.media_type", "必须是非空字符串")
            if not _is_mapping(artifact.get("metadata")):
                _add(errors, f"{path}.metadata", "必须是对象")
    for key in ("console_errors", "network_failures"):
        value = record.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            _add(errors, f"$.{key}", "必须是字符串数组")
    _validate_trust(record, errors)
    return errors


def validate_reference_fusion(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """校验 deterministic/visual 融合摘要，保留冲突而不生成验收结论。"""

    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("fusion_id"), r"^FUS-[0-9]{6}$", errors, "$.fusion_id")
    _pattern(record.get("reference_id"), _id_pattern(config or load_reference_config(), "reference"), errors, "$.reference_id")
    _pattern(record.get("input_hash"), r"^[a-fA-F0-9]{64}$", errors, "$.input_hash")
    for key in ("selected_source_refs", "visual_source_refs"):
        value = record.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            _add(errors, f"$.{key}", "必须是非空字符串数组或空数组")
    conflicts = record.get("conflicts")
    if not isinstance(conflicts, list):
        _add(errors, "$.conflicts", "必须是数组")
    else:
        for index, conflict in enumerate(conflicts):
            path = f"$.conflicts[{index}]"
            if not _is_mapping(conflict):
                _add(errors, path, "必须是对象")
                continue
            _pattern(conflict.get("conflict_id"), r"^REFCON-[0-9]{3}$", errors, f"{path}.conflict_id")
            for key in ("domain", "category", "resolution"):
                if not isinstance(conflict.get(key), str) or not conflict[key]:
                    _add(errors, f"{path}.{key}", "必须是非空字符串")
            for key in ("deterministic_refs", "visual_refs"):
                value = conflict.get(key)
                if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                    _add(errors, f"{path}.{key}", "必须是非空字符串数组")
            if not isinstance(conflict.get("review_required"), bool):
                _add(errors, f"{path}.review_required", "必须是布尔值")
    limitations = record.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        _add(errors, "$.limitations", "必须是字符串数组")
    _enum(record.get("binding_status"), ("READY", "REVIEW_REQUIRED", "BLOCKED"), errors, "$.binding_status")
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    _validate_trust(record, errors)
    return errors


def validate_visual_conformance(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    """校验视觉比对运行摘要，禁止把视觉结果伪装成 R6 PASS/FAIL。"""

    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("run_id"), r"^VC-[0-9]{6}$", errors, "$.run_id")
    _pattern(record.get("reference_id"), _id_pattern(config or load_reference_config(), "reference"), errors, "$.reference_id")
    _pattern(record.get("approved_binding_ref"), r"^REFDEC-[0-9]{3,4}$", errors, "$.approved_binding_ref")
    hashes = record.get("input_evidence_hashes")
    if not isinstance(hashes, list) or len(hashes) != 2:
        _add(errors, "$.input_evidence_hashes", "必须包含两份截图哈希")
    else:
        for index, item in enumerate(hashes):
            _pattern(item, r"^[a-fA-F0-9]{64}$", errors, f"$.input_evidence_hashes[{index}]")
    _enum(record.get("status"), ("BLOCKED", "SUCCEEDED"), errors, "$.status")
    _enum(record.get("comparison_status"), ("BLOCKED", "UNVERIFIED"), errors, "$.comparison_status")
    if record.get("perception_run_id") is not None:
        _pattern(record.get("perception_run_id"), r"^PER-[0-9]{6}$", errors, "$.perception_run_id")
    if record.get("result_hash") is not None:
        _pattern(record.get("result_hash"), r"^[a-fA-F0-9]{64}$", errors, "$.result_hash")
    limitations = record.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        _add(errors, "$.limitations", "必须是字符串数组")
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    _validate_trust(record, errors)
    return errors


def validate_reference_finding(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("finding_id"), _id_pattern(config, "finding"), errors, "$.finding_id")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    _enum(record.get("domain"), config.get("analysis_domains", []), errors, "$.domain")
    if not isinstance(record.get("category"), str) or not record["category"]:
        _add(errors, "$.category", "必须是非空字符串")
    observation = record.get("observation")
    if not _is_mapping(observation) or "value" not in observation:
        _add(errors, "$.observation", "必须包含 value")
    measurement = observation.get("measurement") if _is_mapping(observation) else None
    if measurement is not None:
        if not _is_mapping(measurement):
            _add(errors, "$.observation.measurement", "必须是对象或 null")
        else:
            _enum(measurement.get("value_type"), ("measured", "estimated_range", "estimated_tolerance"), errors, "$.observation.measurement.value_type")
            if not isinstance(measurement.get("unit"), str) or not measurement["unit"]:
                _add(errors, "$.observation.measurement.unit", "必须是非空单位")
            for key in ("estimate", "minimum", "maximum", "tolerance"):
                if key in measurement and measurement[key] is not None and (not isinstance(measurement[key], (int, float)) or isinstance(measurement[key], bool)):
                    _add(errors, f"$.observation.measurement.{key}", "必须是数字")
            if measurement.get("value_type") == "estimated_range" and not {"minimum", "maximum"} <= set(measurement):
                _add(errors, "$.observation.measurement", "estimated_range 需要 minimum 和 maximum")
            if measurement.get("value_type") == "estimated_tolerance" and not {"estimate", "tolerance"} <= set(measurement):
                _add(errors, "$.observation.measurement", "estimated_tolerance 需要 estimate 和 tolerance")
    _enum(record.get("epistemic_status"), config.get("epistemic_statuses", []), errors, "$.epistemic_status")
    _enum(record.get("confidence"), config.get("confidence_levels", []), errors, "$.confidence")
    evidence_refs = record.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        _add(errors, "$.evidence_refs", "至少需要一个证据引用")
    else:
        for index, item in enumerate(evidence_refs):
            _pattern(item, _id_pattern(config, "evidence"), errors, f"$.evidence_refs[{index}]")
    _enum(record.get("user_scope_status"), config.get("scope_states", []), errors, "$.user_scope_status")
    if record.get("epistemic_status") == "inferred" and not record.get("inference_basis"):
        _add(errors, "$.inference_basis", "inferred 必须记录推断依据")
    if record.get("epistemic_status") == "unknown" and not record.get("unknown_reason"):
        _add(errors, "$.unknown_reason", "unknown 必须说明未知原因")
    _validate_trust(record, errors)
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    return errors


def validate_reference_analysis(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("analysis_id"), _id_pattern(config, "analysis"), errors, "$.analysis_id")
    _pattern(record.get("reference_id"), _id_pattern(config, "reference"), errors, "$.reference_id")
    _safe_project_path(record.get("source_artifact_ref"), errors, "$.source_artifact_ref", ("memory", "change_requests"))
    _safe_project_path(record.get("scope_ref"), errors, "$.scope_ref", ("memory", "change_requests"))
    if not isinstance(record.get("analysis_version"), int) or record["analysis_version"] < 1:
        _add(errors, "$.analysis_version", "必须是正整数")
    _enum(record.get("status"), config.get("analysis_artifact_statuses", []), errors, "$.status")
    domains = record.get("domains")
    if not _is_mapping(domains):
        _add(errors, "$.domains", "必须是包含全部分析维度的对象")
        domains = {}
    for domain in config.get("analysis_domains", []):
        result = domains.get(domain)
        if not _is_mapping(result):
            _add(errors, f"$.domains.{domain}", "必须是对象")
            continue
        _enum(result.get("status"), config.get("domain_statuses", []), errors, f"$.domains.{domain}.status")
        for key, kind in (("finding_ids", "finding"), ("evidence_refs", "evidence")):
            refs = result.get(key)
            if not isinstance(refs, list):
                _add(errors, f"$.domains.{domain}.{key}", "必须是数组")
                continue
            for index, item in enumerate(refs):
                _pattern(item, _id_pattern(config, kind), errors, f"$.domains.{domain}.{key}[{index}]")
    evidence_refs = record.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        _add(errors, "$.evidence_refs", "必须是数组")
    else:
        for index, item in enumerate(evidence_refs):
            _pattern(item, _id_pattern(config, "evidence"), errors, f"$.evidence_refs[{index}]")
    _validate_context(record.get("context"), errors)
    _validate_trust(record, errors)
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    if "run_fingerprint" in record and record.get("run_fingerprint") is not None:
        _pattern(record.get("run_fingerprint"), r"^[a-fA-F0-9]{64}$", errors, "$.run_fingerprint")
    return errors


def validate_reference_synthesis(record: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> list[str]:
    config = config or load_reference_config()
    errors: list[str] = []
    if not _is_mapping(record):
        return ["$: 必须是对象"]
    if record.get("schema_version") != 1:
        _add(errors, "$.schema_version", "必须是 1")
    _pattern(record.get("synthesis_id"), _id_pattern(config, "synthesis"), errors, "$.synthesis_id")
    source_references = record.get("source_references")
    if not isinstance(source_references, list) or not source_references:
        _add(errors, "$.source_references", "至少需要一个来源")
    else:
        for index, item in enumerate(source_references):
            _pattern(item, _id_pattern(config, "reference"), errors, f"$.source_references[{index}]")
    decisions = record.get("decisions")
    if not _is_mapping(decisions):
        _add(errors, "$.decisions", "必须包含 adopt、adapt、avoid")
        decisions = {}
    for bucket in ("adopt", "adapt", "avoid"):
        items = decisions.get(bucket)
        if not isinstance(items, list):
            _add(errors, f"$.decisions.{bucket}", "必须是数组")
            continue
        for index, item in enumerate(items):
            path = f"$.decisions.{bucket}[{index}]"
            if not _is_mapping(item):
                _add(errors, path, "必须是对象")
                continue
            _pattern(item.get("decision_id"), _id_pattern(config, "decision"), errors, f"{path}.decision_id")
            _enum(item.get("domain"), config.get("analysis_domains", []), errors, f"{path}.domain")
            refs = item.get("source_findings")
            if not isinstance(refs, list) or not refs:
                _add(errors, f"{path}.source_findings", "至少需要一个 finding")
            else:
                for ref_index, ref in enumerate(refs):
                    _pattern(ref, _id_pattern(config, "finding"), errors, f"{path}.source_findings[{ref_index}]")
            if not isinstance(item.get("rationale"), str) or not item["rationale"]:
                _add(errors, f"{path}.rationale", "必须是非空字符串")
            _enum(item.get("decision_source"), ("reference_finding", "user_explicit_exclusion", "system_policy"), errors, f"{path}.decision_source")
            _enum(item.get("user_scope_status"), config.get("scope_states", []), errors, f"{path}.user_scope_status")
    unknown = record.get("unknown")
    if not isinstance(unknown, list):
        _add(errors, "$.unknown", "必须是数组")
    else:
        for index, item in enumerate(unknown):
            path = f"$.unknown[{index}]"
            if not _is_mapping(item):
                _add(errors, path, "必须是对象")
                continue
            _enum(item.get("domain"), config.get("analysis_domains", []), errors, f"{path}.domain")
            for key in ("statement", "reason"):
                if not isinstance(item.get(key), str) or not item[key]:
                    _add(errors, f"{path}.{key}", "必须是非空字符串")
            refs = item.get("source_findings")
            if not isinstance(refs, list) or not refs:
                _add(errors, f"{path}.source_findings", "至少需要一个 finding")
    conflicts = record.get("conflicts", [])
    if not isinstance(conflicts, list):
        _add(errors, "$.conflicts", "必须是数组")
    else:
        for index, item in enumerate(conflicts):
            path = f"$.conflicts[{index}]"
            if not _is_mapping(item):
                _add(errors, path, "必须是对象")
                continue
            _pattern(item.get("conflict_id"), r"^REFCON-[0-9]{3}$", errors, f"{path}.conflict_id")
            _enum(item.get("domain"), config.get("analysis_domains", []), errors, f"{path}.domain")
            refs = item.get("source_findings")
            if not isinstance(refs, list) or len(refs) < 2:
                _add(errors, f"{path}.source_findings", "冲突至少需要两个 finding")
            else:
                for ref_index, ref in enumerate(refs):
                    _pattern(ref, _id_pattern(config, "finding"), errors, f"{path}.source_findings[{ref_index}]")
            if not isinstance(item.get("statement"), str) or not item["statement"]:
                _add(errors, f"{path}.statement", "必须是非空字符串")
            if item.get("resolution_status") != "requires_planner_resolution":
                _add(errors, f"{path}.resolution_status", "必须要求 Planner 解决")
    policy = record.get("priority_policy")
    if not _is_mapping(policy):
        _add(errors, "$.priority_policy", "必须明确优先级策略")
    else:
        for key in ("explicit_user_requirements_override_reference", "approved_product_artifacts_override_reference", "reference_decision_is_not_requirement"):
            if policy.get(key) is not True:
                _add(errors, f"$.priority_policy.{key}", "必须为 true")
    _validate_context(record.get("context"), errors)
    _validate_trust(record, errors)
    if not isinstance(record.get("created_at"), str) or not record["created_at"]:
        _add(errors, "$.created_at", "必须是非空时间字符串")
    if "run_fingerprint" in record and record.get("run_fingerprint") is not None:
        _pattern(record.get("run_fingerprint"), r"^[a-fA-F0-9]{64}$", errors, "$.run_fingerprint")
    return errors


def _records_by_id(records: Mapping[str, Any] | Iterable[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    if isinstance(records, Mapping):
        values = records.values()
    else:
        values = records
    result: dict[str, Mapping[str, Any]] = {}
    for record in values:
        if isinstance(record, Mapping) and isinstance(record.get(key), str):
            result[record[key]] = record
    return result


def validate_reference_artifact_graph(
    source: Mapping[str, Any],
    scope: Mapping[str, Any],
    analysis: Mapping[str, Any],
    findings: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    evidence: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    synthesis: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    """验证 Source -> Finding -> Evidence -> Synthesis 的无悬空来源链。"""

    config = config or load_reference_config()
    errors = []
    errors.extend(validate_reference_source(source, config))
    errors.extend(validate_reference_scope(scope, config))
    errors.extend(validate_reference_analysis(analysis, config))
    finding_map = _records_by_id(findings, "finding_id")
    evidence_map = _records_by_id(evidence, "evidence_id")
    for finding in finding_map.values():
        errors.extend(validate_reference_finding(finding, config))
    for item in evidence_map.values():
        errors.extend(validate_reference_evidence(item, config))
    errors.extend(validate_reference_synthesis(synthesis, config))
    reference_id = source.get("reference_id")
    if scope.get("reference_id") != reference_id or analysis.get("reference_id") != reference_id:
        _add(errors, "$", "source、scope、analysis 的 reference_id 必须一致")
    if reference_id not in (scope.get("source_refs") or []):
        _add(errors, "$.scope.source_refs", "必须包含当前来源")
    if analysis.get("scope_ref") != source.get("scope_ref"):
        _add(errors, "$.analysis.scope_ref", "必须与 source.scope_ref 一致")
    for finding_id, finding in finding_map.items():
        if finding.get("reference_id") != reference_id:
            _add(errors, f"$.findings.{finding_id}", "finding 不属于当前 reference")
        for evidence_id in finding.get("evidence_refs") or []:
            if evidence_id not in evidence_map:
                _add(errors, f"$.findings.{finding_id}.evidence_refs", f"悬空证据 {evidence_id}")
    for evidence_id, item in evidence_map.items():
        if item.get("reference_id") != reference_id:
            _add(errors, f"$.evidence.{evidence_id}", "evidence 不属于当前 reference")
    for domain, result in (analysis.get("domains") or {}).items():
        for finding_id in (result or {}).get("finding_ids", []):
            if finding_id not in finding_map:
                _add(errors, f"$.analysis.domains.{domain}.finding_ids", f"悬空 finding {finding_id}")
        for evidence_id in (result or {}).get("evidence_refs", []):
            if evidence_id not in evidence_map:
                _add(errors, f"$.analysis.domains.{domain}.evidence_refs", f"悬空 evidence {evidence_id}")
    if analysis.get("evidence_refs"):
        for evidence_id in analysis["evidence_refs"]:
            if evidence_id not in evidence_map:
                _add(errors, "$.analysis.evidence_refs", f"悬空 evidence {evidence_id}")
    if synthesis.get("context") != source.get("context"):
        _add(errors, "$.synthesis.context", "合成记录的上下文必须与来源一致")
    source_refs = set(synthesis.get("source_references") or [])
    if reference_id not in source_refs:
        _add(errors, "$.synthesis.source_references", "必须包含当前来源")
    decisions = synthesis.get("decisions") or {}
    for bucket in ("adopt", "adapt", "avoid"):
        for index, decision in enumerate(decisions.get(bucket) or []):
            for finding_id in decision.get("source_findings") or []:
                finding = finding_map.get(finding_id)
                if finding is None:
                    _add(errors, f"$.synthesis.decisions.{bucket}[{index}]", f"悬空 finding {finding_id}")
                elif finding.get("reference_id") not in source_refs:
                    _add(errors, f"$.synthesis.decisions.{bucket}[{index}]", "decision 的 finding 不在来源集合中")
    for index, item in enumerate(synthesis.get("unknown") or []):
        for finding_id in item.get("source_findings") or []:
            if finding_id not in finding_map:
                _add(errors, f"$.synthesis.unknown[{index}]", f"悬空 finding {finding_id}")
    for index, item in enumerate(synthesis.get("conflicts") or []):
        for finding_id in item.get("source_findings") or []:
            finding = finding_map.get(finding_id)
            if finding is None:
                _add(errors, f"$.synthesis.conflicts[{index}]", f"悬空 finding {finding_id}")
            elif finding.get("reference_id") not in source_refs:
                _add(errors, f"$.synthesis.conflicts[{index}]", "conflict 的 finding 不在来源集合中")
    if source.get("source_origin", {}).get("type") == "system_discovered":
        metadata = source.get("source", {}).get("metadata") or {}
        if metadata.get("explicit_authorization") is not True:
            _add(errors, "$.source.source.metadata.explicit_authorization", "system_discovered 不能自动进入 synthesis")
    return errors


def validate_project_pointer(value: Any) -> list[str]:
    """校验 project.yaml 只保存指向 memory 的相对指针。"""

    if value is None:
        return []
    errors: list[str] = []
    _safe_project_path(value, errors, "$.active_reference_synthesis", ("memory", "change_requests"))
    if isinstance(value, str) and value.startswith("artifacts/"):
        _add(errors, "$.active_reference_synthesis", "project 投影不得直接指向 artifacts")
    return errors


def validate_versioned_artifact_path(value: Any, kind: str) -> list[str]:
    """校验追加式 v1 工件路径；同一文件不得通过固定文件名反复覆盖。"""

    if not isinstance(value, str):
        return ["$: 工件路径必须是字符串"]
    patterns = {
        "source": r"^memory/references/reference-[0-9]{3}/source-[0-9]{3}\.yaml$",
        "scope": r"^memory/references/reference-[0-9]{3}/scope-[0-9]{3}\.yaml$",
        "analysis": r"^memory/references/reference-[0-9]{3}/analysis-[0-9]{3}\.yaml$",
        "finding": r"^memory/references/reference-[0-9]{3}/findings/REFFND-[0-9]{3}\.yaml$",
        "evidence": r"^artifacts/references/reference-[0-9]{3}/evidence/.+$",
        "synthesis": r"^memory/references/synthesis/reference-synthesis-[0-9]{3}\.yaml$",
        "decision": r"^memory/references/synthesis/decisions/REFDEC-[0-9]{3}\.yaml$",
    }
    pattern = patterns.get(kind)
    if pattern is None:
        return [f"$: 未知工件类型 {kind!r}"]
    normalized = value.replace("\\", "/")
    if re.fullmatch(pattern, normalized):
        return []
    if kind in {"source", "scope", "analysis", "finding", "evidence", "synthesis"}:
        change_request_patterns = {
            "source": r"^change_requests/CR-[0-9]{4}/references/reference-[0-9]{3}/source-[0-9]{3}\.yaml$",
            "scope": r"^change_requests/CR-[0-9]{4}/references/reference-[0-9]{3}/scope-[0-9]{3}\.yaml$",
            "analysis": r"^change_requests/CR-[0-9]{4}/references/reference-[0-9]{3}/analysis-[0-9]{3}\.yaml$",
            "finding": r"^change_requests/CR-[0-9]{4}/references/reference-[0-9]{3}/findings/REFFND-[0-9]{3}\.yaml$",
            "evidence": r"^(?:artifacts/references/reference-[0-9]{3}|change_requests/CR-[0-9]{4}/references/reference-[0-9]{3})/evidence/.+$",
            "synthesis": r"^change_requests/CR-[0-9]{4}/references/reference-synthesis-[0-9]{3}\.yaml$",
        }
        if re.fullmatch(change_request_patterns[kind], normalized):
            return []
    return [f"$: 不符合 {kind} 的追加式路径约束"]


def assert_valid(errors: list[str], record_kind: str) -> None:
    """将确定性错误转换为调用方可捕获的协议异常。"""

    if errors:
        raise ReferenceProtocolError(f"{record_kind} INVALID: " + "; ".join(errors))
