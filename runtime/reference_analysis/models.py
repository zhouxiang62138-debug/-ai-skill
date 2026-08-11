"""Reference Analysis 的内部模型，不把原始输入提升为指令。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class NormalizedReference:
    """适配器输出的统一引用模型。"""

    reference_id: str
    source_type: str
    source_artifact_ref: str
    source_hash: str
    scope_ref: str
    context: Mapping[str, Any]
    content: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    available_modalities: tuple[str, ...] = ()
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    trust_level: str = "untrusted"
    adapter_version: int = 1
    acquisition_id: str | None = None
    provider_id: str | None = None
    provider_version: int | None = None
    acquisition_status: str | None = None

    def manifest(self) -> dict[str, Any]:
        """返回可进入上下文、事件和调试输出的非原始内容摘要。"""

        return {
            "reference_id": self.reference_id,
            "source_type": self.source_type,
            "source_artifact_ref": self.source_artifact_ref,
            "source_hash": self.source_hash,
            "scope_ref": self.scope_ref,
            "context": dict(self.context),
            "metadata": dict(self.metadata),
            "available_modalities": list(self.available_modalities),
            "capabilities": dict(self.capabilities),
            "limitations": list(self.limitations),
            "evidence_refs": list(self.evidence_refs),
            "trust_level": self.trust_level,
            "adapter_version": self.adapter_version,
            "acquisition_id": self.acquisition_id,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "acquisition_status": self.acquisition_status,
        }


@dataclass(frozen=True)
class FindingDraft:
    """领域分析器生成的待持久化 Finding。"""

    domain: str
    category: str
    value: str
    epistemic_status: str
    confidence: str
    evidence_refs: tuple[str, ...]
    user_scope_status: str
    notes: str | None = None
    inference_basis: tuple[str, ...] = ()
    unknown_reason: str | None = None


@dataclass(frozen=True)
class AnalyzerResult:
    """领域分析器的纯数据结果。"""

    findings: tuple[FindingDraft, ...]
    supported: bool = True
    limitation: str | None = None


class ReferenceAdapterProtocol:
    """适配器协议的运行时文档接口。"""

    source_type: str
    version: int

    def normalize(self, source: Mapping[str, Any], *, root: Any, path_policy: Any) -> NormalizedReference:
        raise NotImplementedError


class DomainAnalyzerProtocol:
    """领域分析器协议，禁止直接写入项目状态。"""

    domain: str

    def supports(self, normalized: NormalizedReference) -> bool:
        raise NotImplementedError

    def analyze(
        self,
        normalized: NormalizedReference,
        *,
        scope: Mapping[str, str],
        evidence_refs: tuple[str, ...],
    ) -> AnalyzerResult:
        raise NotImplementedError
