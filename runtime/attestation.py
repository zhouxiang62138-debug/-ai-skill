"""Phase Attestation 的 Runtime-owned 结构与摘要工具。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .errors import RuntimeValidationError


def required_steps_hash(steps: tuple[str, ...]) -> str:
    """对 Runtime 最终步骤集合计算稳定摘要。"""

    if not steps or len(set(steps)) != len(steps):
        raise RuntimeValidationError("ATTESTATION_REQUIRED_STEPS_INVALID")
    payload = json.dumps(list(steps), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def attestation_hash(fields: Mapping[str, Any]) -> str:
    """只对受控字段做摘要，不保存模型输出正文。"""

    payload = json.dumps(
        dict(fields), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_verifier_results(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """校验 Verifier 结果和证据引用的最小结构。"""

    if not isinstance(value, Mapping) or not value:
        raise RuntimeValidationError("ATTESTATION_VERIFIER_RESULTS_INVALID")
    safe: dict[str, Any] = {}
    evidence: list[str] = []
    for name, raw in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, Mapping):
            raise RuntimeValidationError("ATTESTATION_VERIFIER_RESULTS_INVALID")
        passed = raw.get("passed")
        refs = raw.get("evidence_refs", [])
        if not isinstance(passed, bool) or not isinstance(refs, list):
            raise RuntimeValidationError("ATTESTATION_VERIFIER_RESULTS_INVALID")
        if any(not isinstance(item, str) or not item.strip() for item in refs):
            raise RuntimeValidationError("ATTESTATION_EVIDENCE_REFS_INVALID")
        if not passed:
            raise RuntimeValidationError("ATTESTATION_VERIFIER_FAILED:" + name)
        safe[name] = {
            "passed": True,
            "evidence_refs": list(refs),
            "details": str(raw.get("details", ""))[:1000],
        }
        evidence.extend(refs)
    return safe, list(dict.fromkeys(evidence))


__all__ = ["attestation_hash", "required_steps_hash", "validate_verifier_results"]
