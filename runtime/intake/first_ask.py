"""First-Ask 的参考检测、登记和路由适配。

First-Ask 只保存用户明确提供的引用事实、范围和使用模式，不读取网页、不分析图片，
也不生成 Finding 或 synthesis。实际分析仍由 Reference Analysis Module 负责。
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from runtime.errors import RuntimeValidationError
from runtime.execution.path_policy import ExecutionPathPolicy
from runtime.orchestrator import Orchestrator
from runtime.project_revision import runtime_projection
from runtime.reference_analysis.artifacts import ReferenceArtifactStore
from runtime.reference_analysis.errors import ReferenceAnalysisError
from runtime.requirements_discovery import (
    DiscoveryArtifactStore,
    analyze_initial_intent,
    apply_question_answers,
    evaluate_research_necessity,
    run_discovery_cycle,
    empty_requirements_snapshot,
)
from scripts.project_state import load_project_state
from scripts.reference_protocol import DOMAINS, load_reference_config


_URL = re.compile(r"https?://[^\s<>\"'\]\)]+", re.IGNORECASE)
_LOCAL_PATH = re.compile(
    r"(?:(?:[A-Za-z]:[\\/])|(?:artifacts|memory|uploads)[\\/])[^\s,;<>\"'\]\)]+",
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_REFERENCE_WORDS = ("参考", "借鉴", "对标", "仿照", "类似", "inspired by", "reference")
_GENERIC_REFERENCE = re.compile(
    r"(?:可能|以后|将来|如果|考虑|也许).{0,12}(?:参考|借鉴|对标|类似).{0,12}(?:产品|网站|应用|项目|别的)",
    re.IGNORECASE,
)
_NAME = re.compile(
    r"(?:参考|借鉴|对标|仿照|类似|inspired\s+by|reference)"
    r"(?:产品|网站|应用|系统|项目)?\s*[:：]?\s*"
    r"([A-Za-z][A-Za-z0-9._ -]{1,40})",
    re.IGNORECASE,
)
_KNOWN_REFERENCE_NAMES = ("Linear", "Notion", "Figma", "Slack", "Vercel", "GitHub", "GitLab", "Trello", "Asana", "Airtable", "Stripe", "ChatGPT")

_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "product": ("产品", "功能", "product", "feature"),
    "information_architecture": ("信息架构", "层级", "信息结构", "information architecture"),
    "navigation": ("导航", "菜单", "侧栏", "navigation", "sidebar"),
    "interaction": ("交互", "点击", "反馈", "interaction", "interaction"),
    "layout": ("布局", "排版", "页面结构", "layout", "grid"),
    "visual_style": ("视觉", "风格", "颜色", "色彩", "主题", "字体", "visual", "style"),
    "components": ("组件", "按钮", "卡片", "component", "button", "card"),
    "design_tokens": ("设计令牌", "间距", "圆角", "阴影", "design token"),
    "motion": ("动画", "动效", "过渡", "motion", "animation"),
    "content_style": ("文案", "内容风格", "语气", "content", "copy"),
    "brand": ("品牌", "logo", "标识", "brand"),
    "technical_architecture": ("技术架构", "技术", "接口", "数据库", "api", "architecture"),
}
_EXCLUSION_WORDS = ("不要", "不需要", "无需", "排除", "避免", "不参考", "不采用", "exclude", "avoid")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\r\n", "\n").split())


def _request_hash(project_id: str, text: str) -> str:
    payload = f"{project_id}\n{_normalize_text(text)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _strip_locator(value: str) -> str:
    return value.rstrip(".,;:!?，。；：！？")


def _scope_for_text(text: str) -> tuple[dict[str, str], list[str]]:
    """把用户明确提到的领域转换成 include/exclude/unspecified 三态。"""

    normalized = text.casefold()
    scope = {domain: "unspecified" for domain in DOMAINS}
    excluded_details: list[str] = []
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        mentioned = any(keyword.casefold() in normalized for keyword in keywords)
        if not mentioned:
            continue
        excluded = False
        for keyword in keywords:
            escaped = re.escape(keyword.casefold())
            before = rf"(?:{'|'.join(map(re.escape, _EXCLUSION_WORDS))})[^，,。；;\n]{{0,24}}{escaped}"
            after = rf"{escaped}[^，,。；;\n]{{0,12}}(?:{'|'.join(map(re.escape, _EXCLUSION_WORDS))})"
            if re.search(before, normalized) or re.search(after, normalized):
                excluded = True
                break
        scope[domain] = "exclude" if excluded else "include"

    if re.search(r"(?:不要|不需要|避免|不参考).{0,12}(?:暗色|黑色|dark\s*theme)", normalized):
        scope["visual_style"] = "exclude"
        excluded_details.append("dark_theme")
    if re.search(r"(?:不要|不需要|避免|不参考).{0,12}(?:品牌色|logo|品牌)", normalized):
        scope["brand"] = "exclude"
        excluded_details.append("brand_identity")
    return scope, excluded_details


def _mode_for_text(text: str) -> str:
    normalized = text.casefold()
    if re.search(r"原样|一比一|尽可能还原|完全复制|close[_ -]?recreation|pixel perfect", normalized):
        return "close_recreation"
    if re.search(r"只借鉴|只参考|仅借鉴|仅参考|灵感|inspiration", normalized):
        return "inspiration"
    if re.search(r"重新设计|适配|改造|融合|adaptation|adapt", normalized):
        return "adaptation"
    config = load_reference_config()
    default_mode = config.get("default_mode", "adaptation")
    return default_mode if default_mode in {"inspiration", "adaptation", "close_recreation"} else "adaptation"


def _explicit_reference_description(text: str) -> bool:
    normalized = _URL.sub("", text).casefold()
    if _GENERIC_REFERENCE.search(normalized):
        return False
    if not any(word.casefold() in normalized for word in _REFERENCE_WORDS):
        return False
    name_match = _NAME.search(normalized)
    valid_name = bool(name_match and not name_match.group(1).strip().startswith("http"))
    if valid_name:
        return True
    if _URL.search(text):
        return False
    return bool(
        re.search(
            r"(?:参考|借鉴|对标|仿照|类似|inspired\s+by|reference).{0,80}"
            r"(?:产品|网站|应用|系统|项目|布局|导航|页面|风格|交互|功能|design|layout|style|navigation)",
            normalized,
            re.IGNORECASE,
        )
    )


@dataclass(frozen=True)
class ReferenceCandidate:
    """First-Ask 识别出的用户引用，不包含任何分析结果。"""

    source_type: str
    locator: str
    detection_kind: str
    phrase: str
    reference_mode: str
    requested_scope: Mapping[str, str]
    excluded_details: tuple[str, ...] = ()
    source_origin: str = "user_provided"

    def to_source(self, request_ref: str) -> dict[str, Any]:
        if self.source_type == "web_page":
            source = {"uri": self.locator}
            origin = "user_linked"
        elif self.source_type == "image":
            source = {"artifact_ref": self.locator}
            origin = "user_uploaded"
        else:
            source = {"text_ref": request_ref, "identifier": self.locator}
            origin = "user_provided"
        source["metadata"] = {
            "registration_request_ref": request_ref,
            "detection_kind": self.detection_kind,
            "detection_phrase": self.phrase,
            "excluded_details": list(self.excluded_details),
        }
        return {
            "source_type": self.source_type,
            "source": source,
            "reference_mode": self.reference_mode,
            "source_origin": {"type": origin, "user_request_ref": request_ref},
        }


def detect_references(
    user_text: str,
    *,
    project_root: str | Path | None = None,
    attached_paths: Iterable[str | Path] = (),
) -> tuple[ReferenceCandidate, ...]:
    """保守检测明确引用；模糊的“以后可能参考别的产品”不会登记。"""

    if not isinstance(user_text, str) or not user_text.strip():
        return ()
    scope, excluded_details = _scope_for_text(user_text)
    mode = _mode_for_text(user_text)
    results: list[ReferenceCandidate] = []
    seen: set[tuple[str, str]] = set()

    for url in _URL.findall(user_text):
        locator = _strip_locator(url)
        key = ("web_page", locator.casefold())
        if key not in seen:
            results.append(ReferenceCandidate("web_page", locator, "web_url", locator, mode, scope, tuple(excluded_details), "user_linked"))
            seen.add(key)

    local_values = [str(item) for item in attached_paths]
    local_values.extend(match.group(0) for match in _LOCAL_PATH.finditer(user_text))
    for raw_path in local_values:
        candidate_path = Path(raw_path)
        if project_root is None:
            continue
        try:
            absolute = candidate_path if candidate_path.is_absolute() else Path(project_root) / candidate_path
            relative = absolute.resolve(strict=True).relative_to(Path(project_root).resolve()).as_posix()
        except (OSError, ValueError):
            continue
        if Path(relative).suffix.casefold() not in _IMAGE_SUFFIXES:
            continue
        key = ("image", relative.casefold())
        if key not in seen:
            results.append(ReferenceCandidate("image", relative, "authorized_local_asset", raw_path, mode, scope, tuple(excluded_details), "user_uploaded"))
            seen.add(key)

    if _explicit_reference_description(user_text):
        names = [name for name in _KNOWN_REFERENCE_NAMES if re.search(rf"\b{re.escape(name)}\b", user_text, re.IGNORECASE)]
        if not names:
            names = [match.group(1).strip(" .") for match in _NAME.finditer(user_text)]
        names = names or ["user_reference_description"]
        for name in names:
            key = ("text_description", name.casefold())
            if key not in seen:
                results.append(ReferenceCandidate("text_description", name, "explicit_reference_description", user_text.strip(), mode, scope, tuple(excluded_details), "user_provided"))
                seen.add(key)
    return tuple(results)


def _write_append_only(path_policy: ExecutionPathPolicy, root: Path, relative: str, content: str) -> None:
    target = path_policy.assert_module_path("first_ask_intake", root, relative, operation="write")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_text(encoding="utf-8") != content:
            raise RuntimeValidationError("FIRST_ASK_APPEND_ONLY_CONFLICT")
        return
    handle, temporary = tempfile.mkstemp(prefix=".first-ask-", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            raise RuntimeValidationError("FIRST_ASK_APPEND_ONLY_CONFLICT")
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _next_requirement_version(root: Path, path_policy: ExecutionPathPolicy, current: int) -> int:
    directory = path_policy.assert_module_path("first_ask_intake", root, "memory/requirements", operation="write")
    highest = max(0, int(current or 0))
    for path in directory.glob("requirements_v*.yaml"):
        match = re.fullmatch(r"requirements_v(\d{3})\.yaml", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def _context_for_state(state: Mapping[str, Any]) -> dict[str, Any]:
    change = state.get("active_change_request")
    if isinstance(change, Mapping):
        request_id = change.get("change_request_id") or change.get("request_id")
        if isinstance(request_id, str) and re.fullmatch(r"CR-[0-9]{4}", request_id):
            return {"type": "change_request", "project_id": str(state["project_id"]), "change_request_id": request_id}
    return {"type": "new_project", "project_id": str(state["project_id"]), "change_request_id": None}


@dataclass(frozen=True)
class FirstAskResult:
    """一次 First-Ask 输入的可审计结果。"""

    request_hash: str
    request_ref: str | None
    reference_ids: tuple[str, ...]
    requirements_ref: str | None
    route: str
    idempotent: bool = False
    activation: str | None = None


class FirstAskIntakeModule:
    """First-Ask Intake Module；不承担 Reference Analysis。"""

    module_name = "first_ask_intake"

    def __init__(self, project_root: str | Path, *, orchestrator: Orchestrator | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.path_policy = ExecutionPathPolicy()
        self.orchestrator = orchestrator or Orchestrator(self.root)

    def _request_ref(self, request_hash: str) -> str:
        return f"memory/requirements/reference-request-{request_hash[:16]}.md"

    def _existing_for_request(self, store: ReferenceArtifactStore, context: Mapping[str, Any], request_hash: str) -> list[dict[str, Any]]:
        matches = []
        for source in store.list_sources(context):
            metadata = (source.get("source") or {}).get("metadata", {})
            if isinstance(metadata, Mapping) and metadata.get("registration_request_hash") == request_hash:
                matches.append(source)
        return sorted(matches, key=lambda item: str(item.get("reference_id")))

    def _write_requirements_snapshot(
        self,
        state: Mapping[str, Any],
        user_text: str,
        request_ref: str,
        registered: list[Mapping[str, Any]],
    ) -> tuple[str, int]:
        current_ref = state.get("active_requirements")
        base: dict[str, Any] = {}
        if isinstance(current_ref, str) and current_ref:
            current_path = self.path_policy.assert_module_path("first_ask_intake", self.root, current_ref, operation="read")
            try:
                loaded = yaml.safe_load(current_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise RuntimeValidationError("FIRST_ASK_REQUIREMENTS_READ_FAILED") from exc
            if isinstance(loaded, dict):
                base = copy.deepcopy(loaded)
        version = _next_requirement_version(self.root, self.path_policy, int(state.get("requirements_version") or 0))
        requirement_ref = f"memory/requirements/requirements_v{version:03d}.yaml"
        base.update({
            "schema_version": 1,
            "requirement_version": version,
            "project_id": str(state["project_id"]),
            "created_at": _now(),
            "supersedes": current_ref if isinstance(current_ref, str) else None,
            "requirements_status": state.get("requirements_status", "draft"),
        })
        original = base.get("original_request") if isinstance(base.get("original_request"), Mapping) else {}
        original = dict(original)
        original.update({"ref": request_ref, "text": user_text})
        base["original_request"] = original
        references = list(base.get("references") or [])
        known = {str(item.get("reference_id")) for item in references if isinstance(item, Mapping)}
        for source in registered:
            reference_id = str(source["reference_id"])
            if reference_id in known:
                continue
            references.append({
                "reference_id": reference_id,
                "source_ref": source.get("_artifact_ref"),
                "scope_ref": source.get("scope_ref"),
                "source_type": source.get("source_type"),
                "reference_mode": source.get("reference_mode"),
                "requested_scope": dict(source.get("requested_scope") or {}),
                "explicit_inclusions": list(source.get("explicit_inclusions") or []),
                "explicit_exclusions": list(source.get("explicit_exclusions") or []),
                "status": "registered",
                "context": dict(source.get("context") or {}),
            })
        base["references"] = references
        _write_append_only(self.path_policy, self.root, requirement_ref, yaml.safe_dump(base, allow_unicode=True, sort_keys=False))
        return requirement_ref, version

    def _discovery_store(self) -> DiscoveryArtifactStore:
        return DiscoveryArtifactStore(self.root, actor=self.module_name, path_policy=self.path_policy)

    def _next_discovery_ref(self, prefix: str, store: DiscoveryArtifactStore) -> str:
        number = store.next_number("memory/requirements", rf"{re.escape(prefix)}-(\d{{3}})\.yaml")
        return f"memory/requirements/{prefix}-{number:03d}.yaml"

    def _write_discovery_round_anchor(self, round_number: int) -> str:
        store = self._discovery_store()
        reference = f"memory/research/domain/round_{round_number:03d}/research-round.yaml"
        store.write(
            reference,
            {
                "schema_version": 1,
                "round": round_number,
                "status": "planned",
                "created_at": _now(),
                "trust_boundary": "研究轮次锚点，不代表研究已经成功完成。",
            },
        )
        return reference

    def _new_discovery_snapshot(
        self,
        state: Mapping[str, Any],
        *,
        user_text: str,
        request_ref: str,
        registered: Iterable[Mapping[str, Any]],
        version: int,
    ) -> dict[str, Any]:
        snapshot = empty_requirements_snapshot(
            str(state["project_id"]),
            version=version,
            request_ref=request_ref,
            user_text=user_text,
            created_at=_now(),
        )
        references: list[dict[str, Any]] = []
        for source in registered:
            references.append(
                {
                    "reference_id": source.get("reference_id"),
                    "source_ref": source.get("_artifact_ref"),
                    "scope_ref": source.get("scope_ref"),
                    "source_type": source.get("source_type"),
                    "reference_mode": source.get("reference_mode"),
                    "requested_scope": dict(source.get("requested_scope") or {}),
                    "explicit_inclusions": list(source.get("explicit_inclusions") or []),
                    "explicit_exclusions": list(source.get("explicit_exclusions") or []),
                    "status": "registered",
                    "context": dict(source.get("context") or {}),
                }
            )
        snapshot["references"] = references
        return snapshot

    def _load_discovery_snapshot(self, reference: str) -> dict[str, Any]:
        return self._discovery_store().read(reference)

    def _run_discovery_artifacts(
        self,
        state: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        *,
        requirements_ref: str,
        request_text: str,
        round_number: int,
        research_status: str,
        intent: Mapping[str, Any] | None = None,
        research_requirement: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """写入本轮发现工件，并返回新需求快照与发现周期结果。"""

        store = self._discovery_store()
        project_id = str(state["project_id"])
        cycle = run_discovery_cycle(
            snapshot,
            project_id=project_id,
            requirements_ref=requirements_ref,
            request_text=request_text,
            round_number=round_number,
            history_question_keys=[
                str(value.get("last_question_key"))
                for value in snapshot.values()
                if isinstance(value, Mapping) and value.get("last_question_key")
            ],
            research_status=research_status,
            intent=intent,
            research_requirement=research_requirement,
            created_at=_now(),
        )
        artifact_specs = (
            ("intent-analysis", cycle["intent"]),
            ("research-requirement", cycle["research_requirement"]),
            ("coverage-map", cycle["coverage"]),
            ("gap-analysis", cycle["gaps"]),
            ("question-set", cycle["question_set"]),
            ("sufficiency-evaluation", cycle["sufficiency"]),
        )
        refs: dict[str, str] = {}
        for prefix, value in artifact_specs:
            reference = self._next_discovery_ref(prefix, store)
            store.write(reference, value)
            refs[prefix] = reference

        updated = copy.deepcopy(dict(snapshot))
        discovery = dict(updated.get("discovery") or {})
        discovery.update(
            {
                "schema_version": 1,
                "status": "ready" if cycle["sufficiency"]["decision"] == "sufficient_for_planning" else "interviewing",
                "intent_analysis_ref": refs["intent-analysis"],
                "research_requirement_ref": refs["research-requirement"],
                "research_status": research_status,
                "active_research_round": state.get("active_research_round"),
                "research_refs": [state.get("active_research_round")] if state.get("active_research_round") else [],
                "coverage_map_ref": refs["coverage-map"],
                "gap_analysis_ref": refs["gap-analysis"],
                "question_set_ref": refs["question-set"],
                "sufficiency_evaluation_ref": refs["sufficiency-evaluation"],
                "opportunity_map_ref": state.get("opportunity_map_ref"),
            }
        )
        updated["discovery"] = discovery
        updated["discovery_status"] = discovery["status"]
        updated["requirements_status"] = cycle["sufficiency"]["decision"]
        updated["completion_assessment"] = {
            "critical_fields_ready": not bool(cycle["sufficiency"]["blocking_items"]),
            "blocking_fields": list(cycle["sufficiency"]["blocking_items"]),
            "visual_undecided_routed": bool(cycle["sufficiency"]["routed_to_design_exploration"]),
            "notes": "由确定性 Sufficiency Gate 生成；研究证据不自动升级为用户需求。",
        }
        updated["undecided_items"] = [
            {
                "field": item,
                "routing": "design_exploration",
                "source_ref": refs["sufficiency-evaluation"],
            }
            for item in cycle["sufficiency"]["routed_to_design_exploration"]
        ]
        updated["source_interviews"] = list(updated.get("source_interviews") or [])
        updated["discovery_artifact_refs"] = refs
        return updated, {**cycle, "refs": refs}

    def _write_discovery_snapshot(self, snapshot: Mapping[str, Any], *, version: int, supersedes: str | None) -> str:
        store = self._discovery_store()
        reference = f"memory/requirements/requirements_v{version:03d}.yaml"
        content = copy.deepcopy(dict(snapshot))
        content["schema_version"] = 2
        content["requirement_version"] = version
        content["created_at"] = _now()
        content["supersedes"] = supersedes
        store.write(reference, content)
        return reference

    def _commit_discovery_projection(
        self,
        state: Mapping[str, Any],
        *,
        target: str,
        requirements_ref: str,
        requirements_version: int,
        cycle: Mapping[str, Any],
        research_status: str,
        worker_id: str,
    ) -> tuple[str, str | None]:
        sufficiency = cycle["sufficiency"]
        changed: dict[str, Any] = {
            "active_requirements": requirements_ref,
            "requirements_version": requirements_version,
            "requirements_status": sufficiency["decision"],
            "requirements_discovery_status": "researching" if target == "REQUIREMENT_RESEARCH" else "interviewing" if target == "WAITING_FOR_REQUIREMENTS" else "ready",
            "research_status": research_status,
            "intent_analysis_ref": cycle["refs"]["intent-analysis"],
            "research_requirement_ref": cycle["refs"]["research-requirement"],
            "coverage_map_ref": cycle["refs"]["coverage-map"],
            "gap_analysis_ref": cycle["refs"]["gap-analysis"],
            "question_set_ref": cycle["refs"]["question-set"],
            "sufficiency_evaluation_ref": cycle["refs"]["sufficiency-evaluation"],
            "active_interview": cycle["refs"]["question-set"],
            "intake_round": int(state.get("intake_round") or 0) + 1,
        }
        if target == "REFERENCE_ANALYSIS":
            changed["reference_status"] = "ready"
        if target == "REQUIREMENT_RESEARCH":
            changed["active_research_round"] = str(
                cycle.get("research_round_ref")
                or self._write_discovery_round_anchor(int(state.get("intake_round") or 0) + 1)
            )
        elif state.get("active_research_round"):
            changed["active_research_round"] = state.get("active_research_round")
        projection = runtime_projection(state)
        started = self.orchestrator.start(worker_id=worker_id, allow_user_input_module=True)
        if started["selection"].kind != "MODULE" or started["selection"].target != self.module_name:
            raise RuntimeValidationError("FIRST_ASK_MODULE_NOT_SELECTED")
        try:
            self.orchestrator.commit_module_step(
                str(started["session_id"]),
                self.module_name,
                {
                    "project_yaml": str(self.root / "project.yaml"),
                    "source_status": str(state["status"]),
                    "target_status": target,
                    "changed_fields": changed,
                    "expected_revision": int(projection["revision"]),
                    "idempotency_key": f"first-ask-discovery:{projection['revision']}:{requirements_ref}:{target}",
                },
                worker_id=worker_id,
                lease_version=int(started["lease_version"]),
                lease_token=str(started["lease_token"] or ""),
            )
        finally:
            self.orchestrator.leases.release(str(started["session_id"]), worker_id, int(started["lease_version"]), str(started["lease_token"] or ""))
        next_role = "planner" if target == "PLANNING" else None
        return target, next_role

    def _route(self, state: Mapping[str, Any], *, requirements_ref: str | None, requirements_version: int, has_references: bool, worker_id: str) -> tuple[str, str | None]:
        status = str(state.get("status"))
        if status not in {"INTAKE", "WAITING_FOR_REQUIREMENTS"}:
            if status == "REFERENCE_ANALYSIS":
                return status, None
            if status == "PLANNING":
                return status, "planner"
            raise RuntimeValidationError("FIRST_ASK_SOURCE_STATE_INVALID")
        if state.get("requirements_status") == "sufficient_for_planning":
            target = "REFERENCE_ANALYSIS" if has_references else "PLANNING"
        else:
            target = "WAITING_FOR_REQUIREMENTS"
        changed: dict[str, Any] = {
            "status": target,
            "next_role": None if target in {"WAITING_FOR_REQUIREMENTS", "REFERENCE_ANALYSIS"} else "planner",
            "active_module": "first_ask_intake" if target == "WAITING_FOR_REQUIREMENTS" else ("reference_analysis" if target == "REFERENCE_ANALYSIS" else None),
        }
        if requirements_ref:
            changed.update({"active_requirements": requirements_ref, "requirements_version": requirements_version})
        if has_references:
            changed["reference_status"] = "ready"
        projection = runtime_projection(state)
        started = self.orchestrator.start(worker_id=worker_id, allow_user_input_module=True)
        if started["selection"].kind != "MODULE" or started["selection"].target != self.module_name:
            raise RuntimeValidationError("FIRST_ASK_MODULE_NOT_SELECTED")
        try:
            self.orchestrator.commit_module_step(
                str(started["session_id"]),
                self.module_name,
                {
                    "project_yaml": str(self.root / "project.yaml"),
                    "source_status": status,
                    "target_status": target,
                    "changed_fields": changed,
                    "expected_revision": int(projection["revision"]),
                    "idempotency_key": f"first-ask-route:{projection['revision']}:{target}",
                },
                worker_id=worker_id,
                lease_version=int(started["lease_version"]),
                lease_token=str(started["lease_token"] or ""),
            )
        finally:
            self.orchestrator.leases.release(str(started["session_id"]), worker_id, int(started["lease_version"]), str(started["lease_token"] or ""))
        return target, changed["next_role"]

    def process_user_message(self, user_text: str, *, worker_id: str | None = None, attached_paths: Iterable[str | Path] = ()) -> FirstAskResult:
        state = load_project_state(self.root / "project.yaml")
        project_id = str(state["project_id"])
        request_hash = _request_hash(project_id, user_text)
        context = _context_for_state(state)
        store = ReferenceArtifactStore(self.root, path_policy=self.path_policy, path_actor=self.module_name)
        existing = self._existing_for_request(store, context, request_hash)
        request_ref = self._request_ref(request_hash)
        candidates = detect_references(user_text, project_root=self.root, attached_paths=attached_paths)
        if existing:
            target, _ = self._route(
                state,
                requirements_ref=state.get("active_requirements"),
                requirements_version=int(state.get("requirements_version") or 0),
                has_references=True,
                worker_id=worker_id or "first-ask-worker",
            ) if state.get("status") in {"INTAKE", "WAITING_FOR_REQUIREMENTS"} else (str(state.get("status")), state.get("next_role"))
            activation = None
            if target == "REFERENCE_ANALYSIS" and load_project_state(self.root / "project.yaml").get("reference_analysis_status") == "not_started":
                from runtime.reference_analysis import ReferenceAnalysisModule

                activation = ReferenceAnalysisModule(self.root, orchestrator=self.orchestrator).activate(worker_id=worker_id or "reference-analysis-worker")
            return FirstAskResult(request_hash, request_ref, tuple(str(item["reference_id"]) for item in existing), state.get("active_requirements"), target, True, activation)

        _write_append_only(self.path_policy, self.root, request_ref, user_text)
        registered: list[dict[str, Any]] = []
        for candidate in candidates:
            source = candidate.to_source(request_ref)
            source["source"]["metadata"]["registration_request_hash"] = request_hash
            try:
                registered.append(store.register_source(source, context=context, requested_scope=candidate.requested_scope))
            except ReferenceAnalysisError:
                raise
        active_sources = self._existing_for_request(store, context, request_hash)
        requirement_version = int(state.get("requirements_version") or 0)
        discovery_status = str(state.get("requirements_discovery_status") or "not_started")
        if state.get("requirements_status") != "sufficient_for_planning":
            discovery_store = self._discovery_store()
            current_ref = state.get("active_requirements")
            answering_existing = (
                isinstance(current_ref, str)
                and bool(current_ref)
                and isinstance(state.get("question_set_ref"), str)
                and state.get("status") in {"INTAKE", "WAITING_FOR_REQUIREMENTS"}
                and discovery_status in {"researching", "interviewing", "sufficiency_check"}
            )
            if answering_existing:
                base_snapshot = self._load_discovery_snapshot(str(current_ref))
                base_snapshot["active_ref"] = str(current_ref)
                question_set = discovery_store.read(str(state["question_set_ref"]))
                snapshot = apply_question_answers(
                    base_snapshot,
                    question_set,
                    user_text,
                    interview_ref=request_ref,
                )
                request_text = str((base_snapshot.get("original_request") or {}).get("text") or user_text)
            else:
                snapshot = self._new_discovery_snapshot(
                    state,
                    user_text=user_text,
                    request_ref=request_ref,
                    registered=registered,
                    version=requirement_version + 1,
                )
                request_text = user_text
            requirement_version += 1
            requirement_ref_candidate = f"memory/requirements/requirements_v{requirement_version:03d}.yaml"
            research_status = str(state.get("research_status") or "not_started")
            if research_status == "not_started":
                research_status = "not_required"
            prior_intent = None
            prior_gate = None
            if answering_existing:
                try:
                    prior_intent = discovery_store.read(str(state.get("intent_analysis_ref"))) if state.get("intent_analysis_ref") else None
                    prior_gate = discovery_store.read(str(state.get("research_requirement_ref"))) if state.get("research_requirement_ref") else None
                except RuntimeValidationError:
                    prior_intent = None
                    prior_gate = None
            updated_snapshot, cycle = self._run_discovery_artifacts(
                state,
                snapshot,
                requirements_ref=requirement_ref_candidate,
                request_text=request_text,
                round_number=int(state.get("intake_round") or 0) + 1,
                research_status=research_status,
                intent=prior_intent,
                research_requirement=prior_gate,
            )
            gate_decision = str(cycle["research_requirement"].get("decision") or "optional")
            if gate_decision == "required" and research_status in {"not_required", "unavailable", "blocked"} and state.get("status") == "INTAKE" and not answering_existing:
                # 首轮必须先完成 Research Gate；不可用时由 Domain Research 明确记录 unavailable。
                research_status = "planned"
                updated_snapshot["discovery"]["research_status"] = research_status
                target = "REQUIREMENT_RESEARCH"
            elif cycle["sufficiency"]["decision"] == "sufficient_for_planning":
                target = "REFERENCE_ANALYSIS" if (registered or active_sources) else "PLANNING"
            else:
                target = "WAITING_FOR_REQUIREMENTS"
            if target == "REQUIREMENT_RESEARCH":
                research_round_ref = self._write_discovery_round_anchor(int(state.get("intake_round") or 0) + 1)
                updated_snapshot["discovery"]["active_research_round"] = research_round_ref
                updated_snapshot["discovery"]["research_refs"] = [research_round_ref]
                cycle["research_round_ref"] = research_round_ref
            requirements_ref = self._write_discovery_snapshot(
                updated_snapshot,
                version=requirement_version,
                supersedes=str(current_ref) if answering_existing else None,
            )
            if target == "REQUIREMENT_RESEARCH":
                # 研究阶段不直接让用户等一个不明确的空转状态。
                cycle["refs"] = dict(cycle["refs"])
                target, next_role = self._commit_discovery_projection(
                    state,
                    target=target,
                    requirements_ref=requirements_ref,
                    requirements_version=requirement_version,
                    cycle=cycle,
                    research_status=research_status,
                    worker_id=worker_id or "first-ask-worker",
                )
            else:
                target, next_role = self._commit_discovery_projection(
                    state,
                    target=target,
                    requirements_ref=requirements_ref,
                    requirements_version=requirement_version,
                    cycle=cycle,
                    research_status=research_status,
                    worker_id=worker_id or "first-ask-worker",
                )
            activation = None
            if target == "REFERENCE_ANALYSIS":
                from runtime.reference_analysis import ReferenceAnalysisModule

                activation = ReferenceAnalysisModule(self.root, orchestrator=self.orchestrator).activate(worker_id=worker_id or "reference-analysis-worker")
            return FirstAskResult(request_hash, request_ref, tuple(str(item["reference_id"]) for item in registered), requirements_ref, target, False, activation)

        requirements_ref = None
        requirement_version = int(state.get("requirements_version") or 0)
        if registered:
            # 保留已有 Reference Analysis 合同：需求已足够时仍登记引用到传统快照。
            requirements_ref, requirement_version = self._write_requirements_snapshot(
                state,
                user_text,
                request_ref,
                registered,
            )
        target, next_role = self._route(
            state,
            requirements_ref=requirements_ref,
            requirements_version=requirement_version,
            has_references=bool(registered or active_sources),
            worker_id=worker_id or "first-ask-worker",
        )
        activation = None
        if target == "REFERENCE_ANALYSIS":
            from runtime.reference_analysis import ReferenceAnalysisModule

            activation = ReferenceAnalysisModule(self.root, orchestrator=self.orchestrator).activate(worker_id=worker_id or "reference-analysis-worker")
        return FirstAskResult(request_hash, request_ref, tuple(str(item["reference_id"]) for item in registered), requirements_ref, target, False, activation)
