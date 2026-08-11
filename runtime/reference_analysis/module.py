"""Reference Analysis Core Module 的编排入口。"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from scripts.project_state import load_project_state, validate_project_state
from scripts.reference_protocol import (
    assert_valid,
    validate_reference_artifact_graph,
    validate_reference_synthesis,
)
from runtime.event_types import ActorType, EventType
from runtime.orchestrator import Orchestrator
from runtime.project_revision import project_state_hash, runtime_projection
from runtime.execution.path_policy import ExecutionPathPolicy

from .acquisition import (
    AcquisitionStatus,
    AcquisitionManifest,
    AcquisitionManifestStore,
    AcquisitionOutput,
    AcquisitionProviderRegistry,
    AcquisitionRequest,
    LocalImageAcquisitionProvider,
    ProviderCapability,
)
from .artifacts import ReferenceArtifactStore
from .browser_acquisition import BrowserAcquisitionProvider
from .errors import ReferenceAnalysisCrash, ReferenceAnalysisError
from .models import AnalyzerResult, FindingDraft
from .perception import (
    CodexNativeMultimodalPerceptionProvider,
    ImageEvidenceInput,
    PerceptionRequest,
    PerceptionRunStore,
)
from .registry import ReferenceRegistry
from .synthesis import ReferenceSynthesisEngine


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReferenceAnalysisModule:
    """单一受控 Module；不创建第四个 Agent，也不直接写 project.yaml。"""

    module_name = "reference_analysis"

    def __init__(self, project_root: str | Path, *, orchestrator: Orchestrator | None = None) -> None:
        self.root = Path(project_root).resolve()
        self.path_policy = ExecutionPathPolicy()
        self.store = ReferenceArtifactStore(self.root, path_policy=self.path_policy)
        self.registry = ReferenceRegistry()
        self.acquisition_manifests = AcquisitionManifestStore(
            self.root, path_policy=self.path_policy
        )
        image_limits = self.registry.config.get("image_capabilities", {})
        self.acquisition_providers = AcquisitionProviderRegistry(
            (
                LocalImageAcquisitionProvider(
                    max_bytes=int(image_limits.get("max_bytes", 10 * 1024 * 1024)),
                    max_pixels=int(image_limits.get("max_pixels", 100_000_000)),
                ),
                BrowserAcquisitionProvider(),
            )
        )
        self.synthesis_engine = ReferenceSynthesisEngine(config=self.registry.config)
        # 图片语义分析是 ReferenceAnalysisModule 的基础设施，不是第四个 Agent。
        self.perception_provider = CodexNativeMultimodalPerceptionProvider()
        self.perception_runs = PerceptionRunStore(self.root, path_policy=self.path_policy)
        self.orchestrator = orchestrator

    def _runtime(self) -> Orchestrator:
        if self.orchestrator is None:
            self.orchestrator = Orchestrator(self.root)
        return self.orchestrator

    def _default_context(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {"type": "new_project", "project_id": str(state["project_id"]), "change_request_id": None}

    def acquire_source(
        self,
        source: Mapping[str, Any],
        *,
        requested_scope: Mapping[str, str] | None = None,
        context: Mapping[str, Any] | None = None,
        fail_at: str | None = None,
    ) -> tuple[AcquisitionManifest, AcquisitionOutput]:
        """通过 RA7-B 生命周期采集一个项目内图片，不触碰业务状态。"""

        state = load_project_state(self.root / "project.yaml")
        selected_context = dict(context or source.get("context") or self._default_context(state))
        if selected_context.get("project_id") != state.get("project_id"):
            raise ReferenceAnalysisError("ACQUISITION_CROSS_PROJECT_DENIED")
        requested = requested_scope or source.get("requested_scope") or {}
        request = AcquisitionRequest(
            reference_id=str(source.get("reference_id")),
            source_type=str(source.get("source_type")),
            locator=dict(source.get("source") or {}),
            context=selected_context,
            requested_scope=dict(requested),
        )
        provider = self.acquisition_providers.for_source(request.source_type)
        manifest = self.acquisition_manifests.begin(request, provider.capability)
        if manifest.status is AcquisitionStatus.SUCCEEDED:
            output = provider.acquire(
                request,
                root=self.root,
                path_policy=self.path_policy,
            )
            current_refs = tuple(item.artifact_ref for item in output.artifacts)
            if current_refs != manifest.artifact_refs:
                raise ReferenceAnalysisError("ACQUISITION_ARTIFACT_IDENTITY_CHANGED")
            return manifest, output
        if manifest.status in {
            AcquisitionStatus.FAILED,
            AcquisitionStatus.TIMED_OUT,
            AcquisitionStatus.CANCELLED,
            AcquisitionStatus.UNKNOWN_AFTER_CRASH,
        }:
            manifest = self.acquisition_manifests.retry(manifest)
        if manifest.status is not AcquisitionStatus.REQUESTED:
            raise ReferenceAnalysisError("ACQUISITION_IDENTITY_IN_PROGRESS")
        started = self.acquisition_manifests.transition(manifest, AcquisitionStatus.STARTED)
        try:
            if fail_at == "after_start":
                raise ReferenceAnalysisCrash("ACQUISITION_CRASH_AFTER_START")
            output = provider.acquire(
                request,
                root=self.root,
                path_policy=self.path_policy,
            )
            if fail_at == "after_provider":
                raise ReferenceAnalysisCrash("ACQUISITION_CRASH_AFTER_PROVIDER")
            completed = self.acquisition_manifests.transition(
                started,
                AcquisitionStatus.SUCCEEDED,
                artifact_refs=tuple(item.artifact_ref for item in output.artifacts),
            )
            return completed, output
        except ReferenceAnalysisCrash:
            raise
        except ReferenceAnalysisError as exc:
            self.acquisition_manifests.transition(
                started,
                AcquisitionStatus.FAILED,
                error_code=exc.code,
            )
            raise

    def register_source(
        self,
        source: Mapping[str, Any],
        *,
        requested_scope: Mapping[str, str] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """注册来源并返回追加后的 source 记录。"""

        state = load_project_state(self.root / "project.yaml")
        selected_context = dict(context or source.get("context") or self._default_context(state))
        if selected_context.get("project_id") != state.get("project_id"):
            raise ReferenceAnalysisError("REFERENCE_CROSS_PROJECT_CONTEXT_DENIED")
        return self.store.register_source(
            source,
            context=selected_context,
            requested_scope=requested_scope,
        )

    def activate(self, *, worker_id: str = "reference-analysis-worker") -> str:
        """把 First-Ask 刚路由到的模块状态初始化为 running。

        这里只提交模块生命周期状态，不读取或分析来源；真正的分析仍由 run() 完成。
        """

        state = load_project_state(self.root / "project.yaml")
        if state.get("status") != "REFERENCE_ANALYSIS":
            raise ReferenceAnalysisError("REFERENCE_MODULE_STATE_INVALID")
        if state.get("reference_analysis_status") == "running":
            return "already_running"
        if state.get("reference_analysis_status") != "not_started":
            raise ReferenceAnalysisError("REFERENCE_MODULE_STATUS_INVALID")
        runtime = self._runtime()
        started = runtime.start(worker_id=worker_id)
        if started["selection"].kind != "MODULE" or started["selection"].target != self.module_name:
            raise ReferenceAnalysisError("REFERENCE_MODULE_NOT_SELECTED")
        try:
            current = load_project_state(self.root / "project.yaml")
            projection = runtime_projection(current)
            runtime.commit_module_step(
                str(started["session_id"]),
                self.module_name,
                {
                    "project_yaml": str(self.root / "project.yaml"),
                    "source_status": "REFERENCE_ANALYSIS",
                    "target_status": "REFERENCE_ANALYSIS",
                    "changed_fields": {
                        "status": "REFERENCE_ANALYSIS",
                        "next_role": None,
                        "active_module": self.module_name,
                        "reference_analysis_status": "running",
                    },
                    "expected_revision": int(projection["revision"]),
                    "idempotency_key": f"reference-analysis-activate:{projection['revision']}",
                },
                worker_id=worker_id,
                lease_version=int(started["lease_version"]),
                lease_token=str(started["lease_token"] or ""),
            )
            return "activated"
        finally:
            runtime.leases.release(
                str(started["session_id"]),
                worker_id,
                int(started["lease_version"]),
                str(started["lease_token"] or ""),
            )

    def revoke_reference(
        self,
        reference_id: str,
        *,
        reason: str,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """追加撤销记录；下一次 run() 会基于剩余 active sources 生成新 synthesis。"""

        state = load_project_state(self.root / "project.yaml")
        selected_context = dict(context or self._default_context(state))
        if selected_context.get("project_id") != state.get("project_id"):
            raise ReferenceAnalysisError("REFERENCE_CROSS_PROJECT_CONTEXT_DENIED")
        record = self.store.supersede_source(reference_id, context=selected_context, reason=reason)
        return {
            "reference_id": reference_id,
            "status": record["status"],
            "supersedes": record["supersedes"],
            "artifact_ref": record["_artifact_ref"],
        }

    def _select_sources(
        self,
        context: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        state = load_project_state(self.root / "project.yaml")
        all_sources = self.store.list_sources(context)
        if not all_sources:
            raise ReferenceAnalysisError("REFERENCE_SOURCE_NOT_REGISTERED")
        selected_context = dict(context or all_sources[0].get("context") or self._default_context(state))
        sources = [
            item for item in all_sources
            if item.get("context") == selected_context
        ]
        if not sources:
            raise ReferenceAnalysisError("REFERENCE_CONTEXT_SOURCE_MISMATCH")
        if any(item.get("context") != selected_context for item in sources):
            raise ReferenceAnalysisError("REFERENCE_CROSS_CONTEXT_MIX_DENIED")
        return state, sources, selected_context

    def _fingerprint(
        self,
        sources: list[Mapping[str, Any]],
        scopes: Mapping[str, Mapping[str, str]],
        normalized_hashes: Mapping[str, str],
    ) -> str:
        payload = {
            "protocol_version": self.registry.config.get("protocol_version"),
            "analysis_version": 1,
            "sources": [
                {
                    "reference_id": str(source["reference_id"]),
                    "source_hash": normalized_hashes[str(source["reference_id"])],
                    "scope": dict(sorted(scopes[str(source["reference_id"])].items())),
                }
                for source in sorted(sources, key=lambda item: str(item["reference_id"]))
            ],
            "adapters": self.registry.config.get("adapters", {}),
            "analyzers": self.registry.config.get("analyzers", {}),
        }
        return _hash_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))

    def _existing_synthesis(
        self,
        context: Mapping[str, Any],
        fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        state = load_project_state(self.root / "project.yaml")
        pointer = state.get("active_reference_synthesis")
        if isinstance(pointer, str) and pointer:
            try:
                value = self.store.read(pointer)
            except ReferenceAnalysisError:
                value = None
            if isinstance(value, dict) and (fingerprint is None or value.get("run_fingerprint") == fingerprint):
                value["_artifact_ref"] = pointer
                return value
        for value in self.store.list_syntheses(context):
            if fingerprint is None or value.get("run_fingerprint") == fingerprint:
                return value
        return None

    def _next_number_in(self, relative_directory: str, pattern: str) -> int:
        directory = self.root / relative_directory
        highest = 0
        if directory.exists():
            for path in directory.rglob("*.yaml"):
                match = re.fullmatch(pattern, path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return highest + 1

    def _next_global_id(self, prefix: str) -> str:
        highest = 0
        for path in (self.root / "memory" / "references", self.root / "artifacts" / "references", self.root / "change_requests"):
            if not path.exists():
                continue
            for item in path.rglob("*.yaml"):
                try:
                    text = item.read_text(encoding="utf-8")
                except OSError:
                    continue
                for match in re.finditer(rf"{re.escape(prefix)}-([0-9]{{3}})", text):
                    highest = max(highest, int(match.group(1)))
        return f"{prefix}-{highest + 1:03d}"

    def _evidence_record(
        self,
        source: Mapping[str, Any],
        normalized: Any,
        evidence_id: str,
    ) -> dict[str, Any]:
        locator = source.get("source") or {}
        artifact_ref = normalized.source_artifact_ref
        if not isinstance(artifact_ref, str) or not artifact_ref:
            artifact_ref = str(source["_artifact_ref"])
        try:
            path = self.path_policy.assert_module_path("reference_analysis", self.root, artifact_ref, operation="read")
            raw = path.read_bytes()
        except Exception:
            raw = normalized.source_hash.encode("ascii")
        evidence_type = {
            "text_description": "text_fragment",
            "image": "image",
            "web_page": "url_metadata",
        }[str(source["source_type"])]
        return {
            "schema_version": 1,
            "evidence_id": evidence_id,
            "reference_id": str(source["reference_id"]),
            "evidence_type": evidence_type,
            "artifact_ref": artifact_ref,
            "integrity": {"algorithm": "sha256", "sha256": _hash_bytes(raw)},
            "viewport": None,
            "captured_at": None,
            "source_location": None,
            "locator": str(locator.get("text_ref") or locator.get("uri") or artifact_ref),
            "metadata": {
                "source_type": str(source["source_type"]),
                "capabilities": dict(normalized.capabilities),
            },
            "trust_level": "untrusted",
        }

    def _finding_record(
        self,
        source: Mapping[str, Any],
        draft: FindingDraft,
        finding_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "finding_id": finding_id,
            "reference_id": str(source["reference_id"]),
            "domain": draft.domain,
            "category": draft.category,
            "observation": {
                "value": draft.value,
                "measurement": None,
                "notes": draft.notes,
            },
            "epistemic_status": draft.epistemic_status,
            "confidence": draft.confidence,
            "evidence_refs": list(draft.evidence_refs),
            "user_scope_status": draft.user_scope_status,
            "inference_basis": list(draft.inference_basis),
            "unknown_reason": draft.unknown_reason,
            "supersedes": None,
            "trust_level": "untrusted",
            "created_at": _now(),
        }

    def _perceive_image(
        self,
        source: Mapping[str, Any],
        normalized: Any,
        *,
        scope: Mapping[str, str],
        evidence_id: str,
        evidence: Mapping[str, Any],
    ) -> AnalyzerResult:
        """把项目内图片交给独立 Codex 感知 Thread，失败时保守降级为 unknown。"""

        requested_domains = tuple(
            str(domain)
            for domain in self.registry.config.get("analysis_domains", [])
            if scope.get(str(domain), "unspecified") != "exclude"
        )
        if not requested_domains:
            return AnalyzerResult((), supported=True, limitation="ALL_IMAGE_DOMAINS_EXCLUDED")
        integrity = evidence.get("integrity") or {}
        mime_type = str(evidence.get("metadata", {}).get("mime_type") or "image/png")
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            mime_type = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(Path(str(normalized.source_artifact_ref)).suffix.casefold(), "image/png")
        try:
            run, findings = self.perception_provider.perceive(
                PerceptionRequest(
                    reference_id=str(source["reference_id"]),
                    evidence=(
                        ImageEvidenceInput(
                            evidence_id=evidence_id,
                            reference_id=str(source["reference_id"]),
                            artifact_ref=str(normalized.source_artifact_ref),
                            sha256=str(integrity.get("sha256")),
                            mime_type=mime_type,
                        ),
                    ),
                    requested_domains=requested_domains,
                    scope="project-local image reference",
                    explicit_exclusions=tuple(
                        str(domain)
                        for domain in self.registry.config.get("analysis_domains", [])
                        if scope.get(str(domain), "unspecified") == "exclude"
                    ),
                ),
                root=self.root,
                path_policy=self.path_policy,
                run_id=self.perception_runs.next_run_id(),
                run_store=self.perception_runs,
            )
        except ReferenceAnalysisError as exc:
            fallback = self.registry.analyzer("image").analyze(
                normalized,
                scope=scope,
                evidence_refs=(evidence_id,),
            )
            return AnalyzerResult(
                fallback.findings,
                supported=False,
                limitation=exc.code,
            )
        if run.status != "SUCCEEDED":
            fallback = self.registry.analyzer("image").analyze(
                normalized,
                scope=scope,
                evidence_refs=(evidence_id,),
            )
            return AnalyzerResult(
                fallback.findings,
                supported=False,
                limitation=(run.failure_code or run.limitations[0] if run.limitations else "PERCEPTION_UNAVAILABLE"),
            )
        drafts: list[FindingDraft] = []
        for finding in findings:
            observation = finding.get("observation") or {}
            drafts.append(
                FindingDraft(
                    domain=str(finding["domain"]),
                    category=str(finding["category"]),
                    value=str(observation.get("value")),
                    epistemic_status=str(finding["epistemic_status"]),
                    confidence=str(finding["confidence"]),
                    evidence_refs=tuple(str(item) for item in finding["evidence_refs"]),
                    user_scope_status=str(finding.get("user_scope_status", "unspecified")),
                    notes=observation.get("notes"),
                    inference_basis=tuple(str(item) for item in finding.get("inference_basis", [])),
                    unknown_reason=finding.get("unknown_reason"),
                )
            )
        return AnalyzerResult(tuple(drafts), supported=run.status == "SUCCEEDED", limitation=None)

    def _traceability_check(
        self,
        sources: list[Mapping[str, Any]],
        analyses: list[Mapping[str, Any]],
        findings: list[Mapping[str, Any]],
        evidence: list[Mapping[str, Any]],
        synthesis: Mapping[str, Any],
    ) -> None:
        source_ids = {str(item["reference_id"]) for item in sources}
        finding_map = {str(item["finding_id"]): item for item in findings}
        evidence_ids = {str(item["evidence_id"]) for item in evidence}
        if set(synthesis.get("source_references", [])) != source_ids:
            raise ReferenceAnalysisError("REFERENCE_TRACE_SOURCE_MISMATCH")
        for analysis in analyses:
            for domain in analysis.get("domains", {}).values():
                for finding_id in domain.get("finding_ids", []):
                    if finding_id not in finding_map:
                        raise ReferenceAnalysisError("REFERENCE_TRACE_FINDING_MISSING")
                for evidence_id in domain.get("evidence_refs", []):
                    if evidence_id not in evidence_ids:
                        raise ReferenceAnalysisError("REFERENCE_TRACE_EVIDENCE_MISSING")
        for finding in findings:
            if str(finding.get("reference_id")) not in source_ids:
                raise ReferenceAnalysisError("REFERENCE_TRACE_CROSS_SOURCE_FINDING")
            if not set(finding.get("evidence_refs", [])) <= evidence_ids:
                raise ReferenceAnalysisError("REFERENCE_TRACE_FINDING_EVIDENCE_MISSING")
        for bucket in ("adopt", "adapt", "avoid"):
            for decision in synthesis.get("decisions", {}).get(bucket, []):
                if not set(decision.get("source_findings", [])) <= set(finding_map):
                    raise ReferenceAnalysisError("REFERENCE_TRACE_DECISION_MISSING")
        for item in synthesis.get("unknown", []) + synthesis.get("conflicts", []):
            if not set(item.get("source_findings", [])) <= set(finding_map):
                raise ReferenceAnalysisError("REFERENCE_TRACE_SYNTHESIS_MISSING")

    def _commit_blocked(self, runtime: Orchestrator, started: Mapping[str, Any], *, worker_id: str) -> None:
        state = load_project_state(self.root / "project.yaml")
        projection = runtime_projection(state)
        runtime.commit_module_step(
            str(started["session_id"]),
            self.module_name,
            {
                "project_yaml": str(self.root / "project.yaml"),
                "source_status": "REFERENCE_ANALYSIS",
                "target_status": "BLOCKED",
                "changed_fields": {
                    "status": "BLOCKED",
                    "next_role": None,
                    "active_module": None,
                    "reference_analysis_status": "blocked",
                    "active_reference_synthesis": None,
                },
                "expected_revision": int(projection["revision"]),
                "idempotency_key": f"reference-analysis-blocked:{projection['revision']}",
            },
            worker_id=worker_id,
            lease_version=int(started["lease_version"]),
            lease_token=str(started["lease_token"] or ""),
        )

    def run(
        self,
        *,
        context: Mapping[str, Any] | None = None,
        worker_id: str | None = None,
        fail_at: str | None = None,
    ) -> dict[str, Any]:
        """执行一次完整 R2 分析；可在提交前崩溃并安全重试。"""

        state, sources, selected_context = self._select_sources(context)
        if selected_context.get("project_id") != state.get("project_id"):
            raise ReferenceAnalysisError("REFERENCE_CROSS_PROJECT_CONTEXT_DENIED")
        if state.get("status") != "REFERENCE_ANALYSIS":
            pointer = state.get("active_reference_synthesis")
            if isinstance(pointer, str) and pointer:
                return {"status": "idempotent", "synthesis_ref": pointer, "project_state_changed": False}
            raise ReferenceAnalysisError("REFERENCE_MODULE_STATE_INVALID")
        if state.get("active_module") != self.module_name or state.get("next_role") is not None:
            raise ReferenceAnalysisError("REFERENCE_MODULE_ROUTE_INVALID")
        scopes: dict[str, dict[str, str]] = {}
        normalized: dict[str, Any] = {}
        for source in sources:
            scope = self.store.read(str(source["scope_ref"]))
            scopes[str(source["reference_id"])] = dict(scope["requested_scope"])
            normalized[str(source["reference_id"])] = self.registry.adapter(str(source["source_type"])).normalize(
                source, root=self.root, path_policy=self.path_policy
            )
        normalized_hashes = {key: item.source_hash for key, item in normalized.items()}
        fingerprint = self._fingerprint(sources, scopes, normalized_hashes)
        existing = self._existing_synthesis(selected_context, fingerprint)
        if existing is not None:
            return self._finish_existing(existing, selected_context, worker_id=worker_id)
        runtime = self._runtime()
        worker_id = worker_id or f"reference-analysis-worker"
        started = runtime.start(worker_id=worker_id)
        if started["selection"].kind != "MODULE" or started["selection"].target != self.module_name:
            raise ReferenceAnalysisError("REFERENCE_MODULE_NOT_SELECTED")
        try:
            if fail_at == "before_artifact_commit":
                raise ReferenceAnalysisCrash("CRASH_BEFORE_ARTIFACT_COMMIT")
            evidence_records: list[dict[str, Any]] = []
            findings: list[dict[str, Any]] = []
            analyses: list[dict[str, Any]] = []
            analysis_paths: dict[str, str] = {}
            artifact_commits: list[dict[str, Any]] = []
            evidence_number = int(self._next_global_id("REFEV").split("-")[1])
            finding_number = int(self._next_global_id("REFFND").split("-")[1])
            for source in sources:
                reference_id = str(source["reference_id"])
                normalized_ref = normalized[reference_id]
                evidence_id = f"REFEV-{evidence_number:03d}"
                evidence_number += 1
                evidence = self._evidence_record(source, normalized_ref, evidence_id)
                evidence_records.append(evidence)
                if str(source["source_type"]) == "image":
                    result = self._perceive_image(
                        source,
                        normalized_ref,
                        scope=scopes[reference_id],
                        evidence_id=evidence_id,
                        evidence=evidence,
                    )
                else:
                    analyzer = self.registry.analyzer(str(source["source_type"]))
                    result = analyzer.analyze(
                        normalized_ref,
                        scope=scopes[reference_id],
                        evidence_refs=(evidence_id,),
                    )
                analysis_number = self._next_number_in(
                    str(Path(str(source["_artifact_ref"])).parent), r"analysis-([0-9]{3})\.yaml"
                )
                analysis_id = f"REFAN-{analysis_number:03d}"
                domains: dict[str, dict[str, Any]] = {}
                source_findings: list[dict[str, Any]] = []
                for draft in result.findings:
                    finding_id = f"REFFND-{finding_number:03d}"
                    finding_number += 1
                    record = self._finding_record(source, draft, finding_id)
                    source_findings.append(record)
                    findings.append(record)
                for domain in self.registry.config.get("analysis_domains", []):
                    state_value = scopes[reference_id].get(domain, "unspecified")
                    domain_findings = [item for item in source_findings if item["domain"] == domain]
                    if state_value == "exclude":
                        status = "excluded"
                    elif not result.supported:
                        status = "unsupported"
                    elif domain_findings:
                        status = "analyzed" if any(
                            item["epistemic_status"] != "unknown" for item in domain_findings
                        ) else "insufficient_evidence"
                    elif state_value == "include":
                        status = "insufficient_evidence"
                    else:
                        status = "unknown"
                    domains[domain] = {
                        "status": status,
                        "finding_ids": [str(item["finding_id"]) for item in domain_findings],
                        "evidence_refs": [evidence_id] if state_value != "exclude" else [],
                        "note": result.limitation,
                    }
                analysis = {
                    "schema_version": 1,
                    "analysis_id": analysis_id,
                    "reference_id": reference_id,
                    "source_artifact_ref": str(source["_artifact_ref"]),
                    "scope_ref": str(source["scope_ref"]),
                    "analysis_version": 1,
                    "status": "completed",
                    "domains": domains,
                    "evidence_refs": [evidence_id],
                    "created_at": _now(),
                    "supersedes": None,
                    "context": dict(selected_context),
                    "trust_level": "untrusted",
                    "run_fingerprint": fingerprint,
                }
                analyses.append(analysis)
                analysis_paths[reference_id] = (
                    f"{Path(str(source['_artifact_ref'])).parent.as_posix()}/analysis-{analysis_number:03d}.yaml"
                )
            synthesis_id = self._next_global_id("REFSYN")
            synthesis = self.synthesis_engine.synthesize(
                sources,
                scopes,
                findings,
                context=selected_context,
                synthesis_id=synthesis_id,
                run_fingerprint=fingerprint,
            )
            synthesis["supersedes"] = state.get("active_reference_synthesis")
            assert_valid(validate_reference_synthesis(synthesis), "reference_synthesis")
            self._traceability_check(sources, analyses, findings, evidence_records, synthesis)
            if len(sources) == 1:
                assert_valid(
                    validate_reference_artifact_graph(
                        sources[0],
                        self.store.read(str(sources[0]["scope_ref"])),
                        analyses[0],
                        findings,
                        evidence_records,
                        synthesis,
                    ),
                    "reference_artifact_graph",
                )
            if selected_context.get("type") == "change_request":
                relative_base = f"change_requests/{selected_context['change_request_id']}/references"
                reference_base = f"{relative_base}/reference-{int(str(sources[0]['reference_id']).split('-')[1]):03d}"
                evidence_number = self._next_number_in(f"{reference_base}/evidence", r"manifest-([0-9]{3})\.yaml")
                synthesis_number = self._next_number_in(relative_base, r"reference-synthesis-([0-9]{3})\.yaml")
                evidence_path = f"{reference_base}/evidence/manifest-{evidence_number:03d}.yaml"
                synthesis_path = f"{relative_base}/reference-synthesis-{synthesis_number:03d}.yaml"
            else:
                relative_base = "memory/references"
                reference_base = f"artifacts/references/reference-{int(str(sources[0]['reference_id']).split('-')[1]):03d}"
                evidence_number = self._next_number_in(f"{reference_base}/evidence", r"manifest-([0-9]{3})\.yaml")
                synthesis_number = self._next_number_in("memory/references/synthesis", r"reference-synthesis-([0-9]{3})\.yaml")
                evidence_path = f"{reference_base}/evidence/manifest-{evidence_number:03d}.yaml"
                synthesis_path = f"memory/references/synthesis/reference-synthesis-{synthesis_number:03d}.yaml"
            staged_paths = [evidence_path]
            for analysis in analyses:
                source = next(item for item in sources if item["reference_id"] == analysis["reference_id"])
                staged_paths.append(f"{Path(str(source['_artifact_ref'])).parent.as_posix()}/analysis-001.yaml")
                staged_paths.extend(
                    f"{Path(str(source['_artifact_ref'])).parent.as_posix()}/findings/{finding['finding_id']}.yaml"
                    for finding in findings
                    if finding["reference_id"] == analysis["reference_id"]
                )
            staged_paths.append(synthesis_path)
            for path in staged_paths:
                runtime.store.append_event(
                    str(started["session_id"]),
                    EventType.ARTIFACT_STAGED,
                    ActorType.MODULE,
                    self.module_name,
                    idempotency_key=f"reference-artifact-staged:{path}:{fingerprint}",
                    correlation_id=str(started["session_id"]),
                    payload={"path": path, "run_fingerprint": fingerprint},
                )
            artifact_commits.append(self.store.write_evidence_manifest(evidence_path, evidence_records))
            for analysis in analyses:
                source = next(item for item in sources if item["reference_id"] == analysis["reference_id"])
                analysis_path = analysis_paths[str(analysis["reference_id"])]
                artifact_commits.append(self.store.write_analysis(analysis_path, analysis))
                for finding in findings:
                    if finding["reference_id"] != analysis["reference_id"]:
                        continue
                    finding_path = f"{Path(str(source['_artifact_ref'])).parent.as_posix()}/findings/{finding['finding_id']}.yaml"
                    artifact_commits.append(self.store.write_finding(finding_path, finding))
            artifact_commits.append(self.store.write_synthesis(synthesis_path, synthesis))
            for artifact in artifact_commits:
                runtime.store.append_event(
                    str(started["session_id"]),
                    EventType.ARTIFACT_COMMITTED,
                    ActorType.MODULE,
                    self.module_name,
                    idempotency_key=f"reference-artifact:{artifact['path']}:{artifact['sha256']}",
                    correlation_id=str(started["session_id"]),
                    payload={"path": artifact["path"], "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"]},
                )
            if fail_at == "after_artifact_commit_before_cas":
                raise ReferenceAnalysisCrash("CRASH_AFTER_ARTIFACT_COMMIT_BEFORE_CAS")
            if selected_context.get("type") == "change_request":
                return {
                    "status": "completed",
                    "synthesis_ref": synthesis_path,
                    "project_state_changed": False,
                    "runtime_module_cas": "deferred_to_change_request_module",
                }
            current = load_project_state(self.root / "project.yaml")
            projection = runtime_projection(current)
            committed = runtime.commit_module_step(
                str(started["session_id"]),
                self.module_name,
                {
                    "project_yaml": str(self.root / "project.yaml"),
                    "source_status": "REFERENCE_ANALYSIS",
                    "target_status": "PLANNING",
                    "changed_fields": {
                        "status": "PLANNING",
                        "next_role": "planner",
                        "active_module": None,
                        "reference_analysis_status": "completed",
                        "active_reference_synthesis": synthesis_path,
                    },
                    "expected_revision": int(projection["revision"]),
                    "idempotency_key": f"reference-analysis-complete:{fingerprint}",
                },
                worker_id=worker_id,
                lease_version=int(started["lease_version"]),
                lease_token=str(started["lease_token"] or ""),
            )
            committed_state = load_project_state(self.root / "project.yaml")
            runtime.store.create_checkpoint(
                str(started["session_id"]),
                project_revision=int(runtime_projection(committed_state)["revision"]),
                project_state_hash=project_state_hash(committed_state),
                active_role="planner",
                active_module=None,
                status="PLANNING",
                next_role="planner",
                open_transaction_ids=[],
            )
            return {
                "status": "completed",
                "synthesis_ref": synthesis_path,
                "project_state_changed": True,
                "project_revision": int(runtime_projection(committed_state)["revision"]),
                "artifact_count": len(artifact_commits),
            }
        except ReferenceAnalysisCrash:
            raise
        except Exception:
            current = load_project_state(self.root / "project.yaml")
            if current.get("status") == "REFERENCE_ANALYSIS":
                self._commit_blocked(runtime, started, worker_id=worker_id)
            raise
        finally:
            runtime.leases.release(
                str(started["session_id"]),
                worker_id,
                int(started["lease_version"]),
                str(started["lease_token"] or ""),
            )

    def _finish_existing(
        self,
        synthesis: Mapping[str, Any],
        context: Mapping[str, Any],
        *,
        worker_id: str | None,
    ) -> dict[str, Any]:
        pointer = str(synthesis.get("_artifact_ref") or "")
        state = load_project_state(self.root / "project.yaml")
        if state.get("status") != "REFERENCE_ANALYSIS" or context.get("type") == "change_request":
            return {"status": "idempotent", "synthesis_ref": pointer, "project_state_changed": False}
        runtime = self._runtime()
        started = runtime.start(worker_id=worker_id or "reference-analysis-worker")
        if started["selection"].kind != "MODULE":
            return {"status": "idempotent", "synthesis_ref": pointer, "project_state_changed": False}
        try:
            current = load_project_state(self.root / "project.yaml")
            projection = runtime_projection(current)
            committed = runtime.commit_module_step(
                str(started["session_id"]),
                self.module_name,
                {
                    "project_yaml": str(self.root / "project.yaml"),
                    "source_status": "REFERENCE_ANALYSIS",
                    "target_status": "PLANNING",
                    "changed_fields": {
                        "status": "PLANNING",
                        "next_role": "planner",
                        "active_module": None,
                        "reference_analysis_status": "completed",
                        "active_reference_synthesis": pointer,
                    },
                    "expected_revision": int(projection["revision"]),
                    "idempotency_key": f"reference-analysis-complete:{synthesis.get('run_fingerprint')}",
                },
                worker_id=str(started["worker_id"]),
                lease_version=int(started["lease_version"]),
                lease_token=str(started["lease_token"] or ""),
            )
            committed_state = load_project_state(self.root / "project.yaml")
            return {"status": "idempotent", "synthesis_ref": pointer, "project_state_changed": True, "project_revision": int(runtime_projection(committed_state)["revision"])}
        finally:
            runtime.leases.release(str(started["session_id"]), str(started["worker_id"]), int(started["lease_version"]), str(started["lease_token"] or ""))
