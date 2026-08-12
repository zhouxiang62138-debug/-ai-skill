"""F14 Runtime 派生 authority 的只读验证。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from scripts.approval import validate_generator_gate
from scripts.project_state import load_project_state, validate_project_state

from ..errors import RuntimeValidationError
from ..project_revision import runtime_projection


APPROVED_AUTHORITIES = frozenset(
    {
        "APPROVED_REQUIREMENT",
        "APPROVED_PRODUCT_SPEC",
        "APPROVED_PLAN",
        "APPROVED_CHANGE_SCOPE",
    }
)


@dataclass(frozen=True)
class AuthorityProof:
    """Runtime 对一个 authority 声明验证后的最小证明。"""

    authority: str
    source_state_ref: str
    exact_locator: str
    project_revision: int
    project_state_hash: str
    content_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority": self.authority,
            "source_state_ref": self.source_state_ref,
            "exact_locator": self.exact_locator,
            "project_revision": self.project_revision,
            "project_state_hash": self.project_state_hash,
            "content_hash": self.content_hash,
        }


class RuntimeAuthorityVerifier:
    """只从当前 project.yaml 和已批准来源链验证 Artifact authority。

    该验证器不会创建 authority，也不会修改 project.yaml；调用方传入的
    authority 只是待验证声明，验证失败时一律拒绝建立索引。
    """

    def __init__(self, workspace_root: str | Path, *, project_revision: int) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.project_revision = project_revision

    def _state(self) -> tuple[dict[str, Any], int, str]:
        project_yaml = self.workspace_root / "project.yaml"
        if not project_yaml.is_file():
            raise RuntimeValidationError("F14_AUTHORITY_PROJECT_STATE_MISSING")
        try:
            state = load_project_state(project_yaml)
            errors = validate_project_state(state, self.workspace_root)
            runtime = runtime_projection(state)
        except Exception as exc:
            raise RuntimeValidationError("F14_AUTHORITY_PROJECT_STATE_INVALID") from exc
        if errors:
            raise RuntimeValidationError("F14_AUTHORITY_PROJECT_STATE_INVALID")
        revision = runtime.get("revision")
        if not isinstance(revision, int) or revision != self.project_revision:
            raise RuntimeValidationError("F14_AUTHORITY_REVISION_MISMATCH")
        state_hash = hashlib.sha256(project_yaml.read_bytes()).hexdigest()
        return state, revision, state_hash

    @staticmethod
    def _require_approved_status(approval_status: str) -> None:
        if approval_status not in {"APPROVED", "VERIFIED"}:
            raise RuntimeValidationError("F14_AUTHORITY_APPROVAL_STATUS_INVALID")

    @staticmethod
    def _require_locator(
        state: Mapping[str, Any], field: str, locator: str, source_state_ref: str
    ) -> None:
        bound = state.get(field)
        if not isinstance(bound, str) or bound != locator:
            raise RuntimeValidationError("F14_AUTHORITY_BINDING_MISMATCH")
        if source_state_ref != field:
            raise RuntimeValidationError("F14_AUTHORITY_SOURCE_REF_MISMATCH")

    def verify(
        self,
        *,
        authority: str,
        locator: str,
        content_hash: str,
        approval_status: str,
        source_state_ref: str,
    ) -> AuthorityProof:
        if authority in APPROVED_AUTHORITIES or authority == "USER_EXPLICIT":
            state, revision, state_hash = self._state()
        else:
            state = {}
            revision = self.project_revision
            state_hash = ""

        if authority == "APPROVED_REQUIREMENT":
            self._require_locator(state, "active_requirements", locator, source_state_ref)
            if state.get("requirements_status") != "sufficient_for_planning":
                raise RuntimeValidationError("F14_AUTHORITY_REQUIREMENT_NOT_APPROVED")
            self._require_approved_status(approval_status)
        elif authority == "APPROVED_PRODUCT_SPEC":
            self._require_locator(state, "active_product_spec", locator, source_state_ref)
            if state.get("product_spec_status") != "finalized":
                raise RuntimeValidationError("F14_AUTHORITY_PRODUCT_SPEC_NOT_APPROVED")
            self._require_approved_status(approval_status)
        elif authority == "APPROVED_PLAN":
            self._require_locator(state, "approved_plan", locator, source_state_ref)
            if state.get("plan_approval_status") != "approved":
                raise RuntimeValidationError("F14_AUTHORITY_PLAN_NOT_APPROVED")
            if validate_generator_gate(state, self.workspace_root):
                raise RuntimeValidationError("F14_AUTHORITY_PLAN_CHAIN_INVALID")
            self._require_approved_status(approval_status)
        elif authority == "APPROVED_CHANGE_SCOPE":
            self._require_locator(state, "change_approval_record", locator, source_state_ref)
            if not state.get("active_change_request") or not state.get("approved_change_items"):
                raise RuntimeValidationError("F14_AUTHORITY_CHANGE_SCOPE_NOT_APPROVED")
            self._require_approved_status(approval_status)
        elif authority == "USER_EXPLICIT":
            if not source_state_ref or not source_state_ref.startswith("user:"):
                raise RuntimeValidationError("F14_AUTHORITY_USER_PROOF_MISSING")
            self._require_approved_status(approval_status)
        elif authority == "RUNTIME_EVIDENCE":
            if not source_state_ref.startswith("runtime:"):
                raise RuntimeValidationError("F14_AUTHORITY_RUNTIME_PROOF_MISSING")
            if approval_status != "VERIFIED":
                raise RuntimeValidationError("F14_AUTHORITY_EVIDENCE_NOT_VERIFIED")
        elif authority == "GENERATED_ARTIFACT":
            if approval_status not in {"UNKNOWN", "GENERATED"}:
                raise RuntimeValidationError("F14_AUTHORITY_GENERATED_STATUS_INVALID")
        elif authority == "HISTORICAL":
            if not source_state_ref or approval_status not in {"HISTORICAL", "UNKNOWN"}:
                raise RuntimeValidationError("F14_AUTHORITY_HISTORY_PROOF_MISSING")
        elif authority == "UNKNOWN":
            if approval_status != "UNKNOWN":
                raise RuntimeValidationError("F14_AUTHORITY_UNKNOWN_STATUS_INVALID")
        else:
            raise RuntimeValidationError("F14_AUTHORITY_INVALID")

        return AuthorityProof(
            authority=authority,
            source_state_ref=source_state_ref,
            exact_locator=locator,
            project_revision=revision,
            project_state_hash=state_hash,
            content_hash=content_hash,
        )


__all__ = ["APPROVED_AUTHORITIES", "AuthorityProof", "RuntimeAuthorityVerifier"]
