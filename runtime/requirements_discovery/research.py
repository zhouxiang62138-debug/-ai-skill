"""Research-Guided Requirements 的受控研究模块。

研究模块是 Runtime Module，不是第四个 Agent。它只生成可追溯的研究工件，
不会直接修改需求快照中的用户事实，也不会替 Planner 做产品决策。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol

from runtime.errors import RuntimeValidationError
from runtime.execution.path_policy import ExecutionPathPolicy
from runtime.orchestrator import Orchestrator
from runtime.project_revision import runtime_projection
from scripts.project_state import load_project_state

from .artifacts import DiscoveryArtifactStore
from .opportunity import build_opportunity_map


class ResearchUnavailable(RuntimeValidationError):
    """当前环境没有可审计的研究适配器或研究服务。"""

    def __init__(self, reason: str = "RESEARCH_ADAPTER_UNAVAILABLE") -> None:
        super().__init__(reason)
        self.reason = reason


class DomainResearchAdapter(Protocol):
    """可注入的研究适配器；适配器不能接收原始隐私内容。"""

    def search(self, query: str, *, max_results: int) -> Iterable[Mapping[str, Any]]:
        ...


class UnavailableResearchAdapter:
    """默认适配器，明确返回 unavailable，而不是猜测或伪造来源。"""

    def search(self, query: str, *, max_results: int) -> Iterable[Mapping[str, Any]]:
        raise ResearchUnavailable()


@dataclass(frozen=True)
class ResearchRunResult:
    research_id: str
    round_number: int
    status: str
    plan_ref: str
    summary_ref: str
    source_refs: tuple[str, ...]
    finding_refs: tuple[str, ...]
    changed_fields: Mapping[str, Any]


_SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|bearer\s+[A-Za-z0-9._-]+|password\s*[:=]|"
    r"secret\s*[:=]|private[_ -]?key|credential\s*[:=])"
)
_QUERY_WORDS = {
    "sales_analytics": "销售分析软件的常见工作流与数据约束",
    "inventory": "库存管理软件的常见工作流与数据约束",
    "finance": "财务软件的常见工作流与审计约束",
    "crm": "客户关系管理软件的常见工作流与数据约束",
    "erp": "企业管理软件的常见工作流与权限约束",
    "healthcare": "医疗软件的常见工作流与隐私约束",
    "education": "教育软件的常见工作流与角色约束",
    "ecommerce": "电商软件的常见工作流与订单约束",
    "logistics": "物流软件的常见工作流与追踪约束",
    "financial": "金融软件的常见工作流与合规约束",
    "general": "该产品类型的常见用户工作流、数据约束与风险边界",
}
_SOURCE_DEFAULTS = {
    "official_documentation": ("tier_1", "primary"),
    "standard": ("tier_1", "primary"),
    "regulator": ("tier_1", "primary"),
    "established_product": ("tier_2", "established"),
    "professional_source": ("tier_3", "professional"),
    "community_signal": ("tier_4", "community"),
}
_ALLOWED_SOURCE_TYPES = frozenset(_SOURCE_DEFAULTS)
_ALLOWED_TIERS = frozenset({"tier_1", "tier_2", "tier_3", "tier_4"})
_ALLOWED_AUTHORITIES = frozenset({"primary", "established", "professional", "community"})
_ALLOWED_CONFIDENCE = frozenset({"high", "medium", "low"})
_ALLOWED_REQUIREMENT_EFFECTS = frozenset({"not_a_requirement", "candidate_supports", "candidate_contradicts", "unknown"})


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id(prefix: str, project_id: str, round_number: int, ordinal: int = 0) -> str:
    digest = hashlib.sha256(f"{project_id}:{round_number}:{ordinal}".encode("utf-8")).hexdigest()
    return f"{prefix}-{int(digest[:6], 16) % 1000:03d}"


def _safe_query(query: str) -> str:
    normalized = " ".join(str(query).split())
    if not normalized or len(normalized) > 240 or _SECRET_PATTERN.search(normalized):
        raise RuntimeValidationError("RESEARCH_QUERY_PRIVACY_VIOLATION")
    return normalized


def _budget(profile: str) -> dict[str, Any]:
    return {
        "simple": {"max_queries": 4, "max_sources": 6, "max_same_domain_sources": 2},
        "standard": {"max_queries": 8, "max_sources": 12, "max_same_domain_sources": 3},
        "deep": {"max_queries": 12, "max_sources": 20, "max_same_domain_sources": 4},
    }.get(profile, {"max_queries": 8, "max_sources": 12, "max_same_domain_sources": 3}) | {"profile": profile}


def build_research_queries(intent: Mapping[str, Any], *, profile: str = "standard") -> list[str]:
    """只根据领域候选生成抽象查询，不把用户原文传给外部适配器。"""

    raw_domains = intent.get("domain_candidates")
    if not isinstance(raw_domains, (list, tuple)):
        domain_value = intent.get("domain")
        raw_domains = [domain_value.get("value")] if isinstance(domain_value, Mapping) else []
    domains = [str(item) for item in raw_domains if str(item)] or ["general"]
    limit = int(_budget(profile)["max_queries"])
    queries: list[str] = []
    for domain in domains:
        query = _QUERY_WORDS.get(domain, f"{domain} 产品的常见用户工作流、数据约束与风险边界")
        safe = _safe_query(query)
        if safe not in queries:
            queries.append(safe)
        if len(queries) >= limit:
            break
    return queries or [_safe_query(_QUERY_WORDS["general"])]


def _result_value(result: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = result.get(key, default)
    return value if value not in (None, "") else default


class DomainResearchModule:
    """在 F10 Lease/CAS 下执行一轮受控研究，并写入 append-only 工件。"""

    module_name = "domain_research"

    def __init__(
        self,
        project_root: str,
        *,
        orchestrator: Orchestrator | None = None,
        adapter: DomainResearchAdapter | None = None,
        path_policy: ExecutionPathPolicy | None = None,
    ) -> None:
        from pathlib import Path

        self.root = Path(project_root).resolve()
        self.path_policy = path_policy or ExecutionPathPolicy()
        self.orchestrator = orchestrator or Orchestrator(self.root)
        self.adapter = adapter or UnavailableResearchAdapter()
        self.store = DiscoveryArtifactStore(
            self.root,
            actor=self.module_name,
            path_policy=self.path_policy,
        )

    def _state(self) -> dict[str, Any]:
        state = load_project_state(self.root / "project.yaml")
        if state.get("status") != "REQUIREMENT_RESEARCH" or state.get("active_module") != self.module_name:
            raise RuntimeValidationError("DOMAIN_RESEARCH_SOURCE_STATE_INVALID")
        return state

    def _load_pointer(self, state: Mapping[str, Any], field: str) -> dict[str, Any]:
        reference = state.get(field)
        if not isinstance(reference, str) or not reference:
            raise RuntimeValidationError(f"DOMAIN_RESEARCH_{field.upper()}_MISSING")
        return self.store.read(reference)

    def _write_plan(
        self,
        *,
        project_id: str,
        round_number: int,
        intent: Mapping[str, Any],
        gate: Mapping[str, Any],
        supersedes: str | None,
        created_at: str,
    ) -> tuple[str, dict[str, Any]]:
        research_id = _id("RSRCH", project_id, round_number)
        profile = str(gate.get("budget_profile") or "standard")
        queries = build_research_queries(intent, profile=profile)
        raw_domains = intent.get("domain_candidates")
        if not isinstance(raw_domains, (list, tuple)):
            domain_value = intent.get("domain")
            raw_domains = [domain_value.get("value")] if isinstance(domain_value, Mapping) else []
        plan = {
            "schema_version": 1,
            "research_id": research_id,
            "project_id": project_id,
            "round": round_number,
            "status": "planned",
            "queries": queries,
            "target_domains": [str(item) for item in raw_domains if str(item)],
            "budget": _budget(profile),
            "created_at": created_at,
            "supersedes": supersedes,
        }
        plan_ref = f"memory/research/domain/round_{round_number:03d}/research-plan.yaml"
        self.store.write(plan_ref, plan)
        return plan_ref, plan

    def _normalize_source(
        self,
        result: Mapping[str, Any],
        *,
        research_id: str,
        ordinal: int,
        created_at: str,
    ) -> dict[str, Any]:
        source_type = str(_result_value(result, "source_type", "professional_source"))
        if source_type not in _ALLOWED_SOURCE_TYPES:
            raise RuntimeValidationError("DOMAIN_RESEARCH_SOURCE_TYPE_INVALID")
        tier, authority = _SOURCE_DEFAULTS[source_type]
        locator = str(_result_value(result, "locator", "adapter://unavailable"))
        if not locator or locator.lower().startswith(("file:", "memory:", "secret:")) or not locator.lower().startswith(("https://", "adapter://")):
            raise RuntimeValidationError("DOMAIN_RESEARCH_SOURCE_LOCATOR_INVALID")
        final_tier = str(_result_value(result, "tier", tier))
        final_authority = str(_result_value(result, "authority_level", authority))
        if final_tier not in _ALLOWED_TIERS or final_authority not in _ALLOWED_AUTHORITIES:
            raise RuntimeValidationError("DOMAIN_RESEARCH_SOURCE_TRUST_LEVEL_INVALID")
        return {
            "schema_version": 1,
            "source_id": f"RRSRC-{ordinal:03d}",
            "research_id": research_id,
            "source_type": source_type,
            "tier": final_tier,
            "authority_level": final_authority,
            "title": _result_value(result, "title"),
            "locator": locator,
            "canonical_locator": _result_value(result, "canonical_locator"),
            "source_origin": "system_discovered",
            "retrieved_at": str(_result_value(result, "retrieved_at", created_at)),
            "content_hash": _result_value(result, "content_hash"),
            "supported_claims": [str(item) for item in (_result_value(result, "supported_claims", []) or [])],
            "trust_level": "untrusted",
            "created_at": created_at,
            "supersedes": None,
        }

    def _normalize_finding(
        self,
        result: Mapping[str, Any],
        *,
        research_id: str,
        source_id: str,
        ordinal: int,
        created_at: str,
    ) -> dict[str, Any]:
        epistemic = str(_result_value(result, "epistemic_status", "OBSERVATION")).upper()
        if epistemic not in {"FACT", "PATTERN", "OBSERVATION", "INFERENCE", "OPPORTUNITY", "IDEA"}:
            raise RuntimeValidationError("DOMAIN_RESEARCH_EPISTEMIC_STATUS_INVALID")
        claim = str(_result_value(result, "claim", "研究适配器未提供可审计结论"))
        if not claim or _SECRET_PATTERN.search(claim):
            raise RuntimeValidationError("DOMAIN_RESEARCH_FINDING_PRIVACY_VIOLATION")
        source_refs = [str(item) for item in (_result_value(result, "source_refs", [source_id]) or [source_id])]
        if source_id not in source_refs:
            source_refs.insert(0, source_id)
        confidence = str(_result_value(result, "confidence", "low"))
        requirement_effect = str(_result_value(result, "requirement_effect", "not_a_requirement"))
        if confidence not in _ALLOWED_CONFIDENCE or requirement_effect not in _ALLOWED_REQUIREMENT_EFFECTS:
            raise RuntimeValidationError("DOMAIN_RESEARCH_FINDING_CLASSIFICATION_INVALID")
        return {
            "schema_version": 1,
            "finding_id": f"RRFND-{ordinal:03d}",
            "research_id": research_id,
            "epistemic_status": epistemic,
            "domain": str(_result_value(result, "domain", "general")),
            "claim": claim,
            "source_refs": source_refs,
            "evidence_refs": [str(item) for item in (_result_value(result, "evidence_refs", []) or [])],
            "confidence": confidence,
            "requirement_effect": requirement_effect,
            "created_at": created_at,
            "supersedes": None,
        }

    def run(self, *, worker_id: str = "domain-research-worker") -> ResearchRunResult:
        state = self._state()
        project_id = str(state["project_id"])
        round_reference = str(state.get("active_research_round") or "")
        round_match = re.search(r"round_(\d{3})", round_reference)
        round_number = int(round_match.group(1)) if round_match else int(round_reference or 0)
        if round_number < 1:
            raise RuntimeValidationError("DOMAIN_RESEARCH_ROUND_INVALID")
        intent = self._load_pointer(state, "intent_analysis_ref")
        gate = self._load_pointer(state, "research_requirement_ref")
        created_at = _now()
        try:
            anchor = self.store.read(round_reference)
            if isinstance(anchor.get("created_at"), str) and anchor["created_at"]:
                # 研究轮次锚点提供稳定时间戳，崩溃恢复重试不会因时间变化覆盖工件。
                created_at = str(anchor["created_at"])
        except RuntimeValidationError:
            pass
        previous_plan = state.get("active_research_round")
        plan_ref, plan = self._write_plan(
            project_id=project_id,
            round_number=round_number,
            intent=intent,
            gate=gate,
            supersedes=previous_plan,
            created_at=created_at,
        )
        profile = str(gate.get("budget_profile") or "standard")
        budget = _budget(profile)
        source_refs: list[str] = []
        finding_refs: list[str] = []
        status = "completed"
        try:
            raw_results: list[Mapping[str, Any]] = []
            for query in plan["queries"][: int(budget["max_queries"])]:
                for item in self.adapter.search(query, max_results=int(budget["max_sources"])):
                    if isinstance(item, Mapping):
                        raw_results.append(item)
                    if len(raw_results) >= int(budget["max_sources"]):
                        break
                if len(raw_results) >= int(budget["max_sources"]):
                    break
            for index, result in enumerate(raw_results, start=1):
                source = self._normalize_source(result, research_id=str(plan["research_id"]), ordinal=index, created_at=created_at)
                source_ref = f"memory/research/domain/round_{round_number:03d}/source-{index:03d}.yaml"
                self.store.write(source_ref, source)
                source_refs.append(source_ref)
                findings = result.get("findings", [])
                if isinstance(findings, Mapping):
                    findings = [findings]
                for finding_offset, finding_result in enumerate(findings or [], start=1):
                    if not isinstance(finding_result, Mapping):
                        continue
                    finding_number = len(finding_refs) + 1
                    finding = self._normalize_finding(
                        finding_result,
                        research_id=str(plan["research_id"]),
                        source_id=str(source["source_id"]),
                        ordinal=finding_number,
                        created_at=created_at,
                    )
                    finding_ref = f"memory/research/domain/round_{round_number:03d}/finding-{finding_number:03d}.yaml"
                    self.store.write(finding_ref, finding)
                    finding_refs.append(finding_ref)
            if not raw_results:
                status = "partially_completed"
        except ResearchUnavailable:
            status = "unavailable"
        except RuntimeValidationError:
            raise
        except Exception as exc:
            status = "blocked"
            failure_ref = f"memory/research/domain/round_{round_number:03d}/research-failure.md"
            self.store.write(failure_ref, {"status": status, "reason": type(exc).__name__, "created_at": created_at})

        summary_ref = f"memory/research/domain/round_{round_number:03d}/research-summary.yaml"
        opportunity_ref: str | None = None
        if finding_refs:
            findings_for_opportunities = []
            for finding_ref in finding_refs:
                findings_for_opportunities.append(self.store.read(finding_ref))
            opportunity = build_opportunity_map(
                findings_for_opportunities,
                project_id=project_id,
                opportunity_id=_id("OPP", project_id, round_number),
                creativity_profile="balanced",
                created_at=created_at,
            )
            opportunity_ref = f"memory/research/domain/round_{round_number:03d}/opportunity-map.yaml"
            self.store.write(opportunity_ref, opportunity)
        summary = {
            "schema_version": 1,
            "research_id": plan["research_id"],
            "project_id": project_id,
            "round": round_number,
            "status": status,
            "plan_ref": plan_ref,
            "source_refs": source_refs,
            "finding_refs": finding_refs,
            "opportunity_map_ref": opportunity_ref,
            "query_count": len(plan["queries"]),
            "source_count": len(source_refs),
            "created_at": created_at,
            "trust_boundary": "研究输出是外部证据，不等于用户需求；Planner 必须分层消费。",
        }
        self.store.write(summary_ref, summary)
        target = "WAITING_FOR_USER" if status in {"blocked"} and str(gate.get("decision")) == "required" else "INTAKE"
        research_projection = {
            "research_status": status,
            "requirements_discovery_status": "interviewing" if target == "INTAKE" else "blocked",
            "active_research_round": summary_ref,
        }
        if opportunity_ref:
            research_projection["opportunity_map_ref"] = opportunity_ref
        projection = runtime_projection(state)
        started = self.orchestrator.start(worker_id=worker_id)
        if started["selection"].kind != "MODULE" or started["selection"].target != self.module_name:
            raise RuntimeValidationError("DOMAIN_RESEARCH_MODULE_NOT_SELECTED")
        try:
            self.orchestrator.commit_module_step(
                str(started["session_id"]),
                self.module_name,
                {
                    "project_yaml": str(self.root / "project.yaml"),
                    "source_status": "REQUIREMENT_RESEARCH",
                    "target_status": target,
                    "changed_fields": research_projection,
                    "expected_revision": int(projection["revision"]),
                    "idempotency_key": f"domain-research:{project_id}:{round_number}:{status}",
                },
                worker_id=worker_id,
                lease_version=int(started["lease_version"]),
                lease_token=str(started["lease_token"] or ""),
            )
        finally:
            self.orchestrator.leases.release(str(started["session_id"]), worker_id, int(started["lease_version"]), str(started["lease_token"] or ""))
        return ResearchRunResult(
            research_id=str(plan["research_id"]),
            round_number=round_number,
            status=status,
            plan_ref=plan_ref,
            summary_ref=summary_ref,
            source_refs=tuple(source_refs),
            finding_refs=tuple(finding_refs),
            changed_fields=research_projection,
        )
