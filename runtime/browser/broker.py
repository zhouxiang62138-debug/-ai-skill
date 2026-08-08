"""连接 F10 Session/Lease、F12 Capability 与 Browser Harness 的 Broker。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from runtime.errors import RuntimeStorageError, RuntimeValidationError
from runtime.event_types import ActorType, EventType
from runtime.execution.models import ExecutionContext
from runtime.leases import LeaseManager
from runtime.policy import CapabilityPolicy
from runtime.session_store import SessionStore

from .adapter import BrowserAdapter
from .harness import BrowserHarness
from .policy import BrowserPolicy


class BrowserBroker:
    """Browser 的唯一 Host-side 入口，不创建新的 Agent。"""

    def __init__(
        self,
        store: SessionStore,
        leases: LeaseManager,
        *,
        browser_policy: BrowserPolicy | None = None,
        capability_policy: CapabilityPolicy | None = None,
    ) -> None:
        self._store = store
        self._leases = leases
        capability_policy = capability_policy or CapabilityPolicy()
        self._policy = browser_policy or BrowserPolicy(
            capability_policy=capability_policy
        )

    def _authorize_context(self, context: ExecutionContext, lease_token: str) -> None:
        session = self._store.get_session(context.session_id)
        if session.project_id != context.project_id:
            raise RuntimeValidationError("BROWSER_PROJECT_ID_MISMATCH")
        if Path(session.project_root).resolve() != Path(context.project_root).resolve():
            raise RuntimeValidationError("BROWSER_PROJECT_ROOT_MISMATCH")
        run = self._store.get_role_run(context.session_id, context.run_id)
        if run["status"] != "STARTED":
            raise RuntimeValidationError("BROWSER_ROLE_RUN_INVALID")
        if run["worker_id"] != context.worker_id or run["role"] != context.role:
            raise RuntimeValidationError("BROWSER_ROLE_RUN_CONTEXT_MISMATCH")
        self._leases.assert_valid(
            context.session_id,
            context.worker_id,
            context.lease_version,
            lease_token,
        )

    def _record_capability_denied(
        self,
        context: ExecutionContext,
        error: RuntimeValidationError,
        resource: str | None,
    ) -> None:
        def digest(value: str | None) -> str | None:
            if value is None:
                return None
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        payload = {
            "role": context.role
            if context.role in {"planner", "generator", "evaluator"}
            else "unknown",
            "capability": "browser.access",
            "decision": "DENY",
            "error_code": str(error),
            "role_hash": digest(context.role),
            "resource_hash": digest(resource),
            "action_hash": digest("browser.acceptance"),
        }
        key = hashlib.sha256(
            "\x1f".join(
                (context.run_id, context.role, "browser.access", resource or "")
            ).encode("utf-8")
        ).hexdigest()
        try:
            self._store.append_event(
                context.session_id,
                EventType.CAPABILITY_DENIED,
                ActorType.ROLE,
                context.role,
                idempotency_key=f"browser-capability-denied:{key}",
                correlation_id=context.run_id,
                payload=payload,
            )
        except Exception as exc:
            raise RuntimeStorageError("BROWSER_CAPABILITY_AUDIT_FAILED") from exc

    def create(
        self,
        context: ExecutionContext,
        *,
        profile: Mapping[str, Any],
        evaluation_id: str,
        browser_run_id: str,
        requirement_id: str,
        acceptance_criterion_id: str,
        lease_token: str,
        scenario_id: str | None = None,
        adapter: BrowserAdapter | None = None,
    ) -> BrowserHarness:
        """校验全部边界后创建 Harness；真正启动由 Harness.start 完成。"""

        self._authorize_context(context, lease_token)
        raw_profile = profile.get("browser_validation", profile)
        base_url = raw_profile.get("base_url") if isinstance(raw_profile, Mapping) else None
        try:
            parsed = self._policy.authorize(context.role, profile)
        except RuntimeValidationError as exc:
            self._record_capability_denied(context, exc, base_url)
            raise
        return BrowserHarness(
            policy=self._policy,
            profile=parsed,
            evaluation_id=evaluation_id,
            browser_run_id=browser_run_id,
            requirement_id=requirement_id,
            acceptance_criterion_id=acceptance_criterion_id,
            scenario_id=scenario_id,
            project_root=context.project_root,
            adapter=adapter,
        )

    def start(self, context: ExecutionContext, **kwargs: Any) -> BrowserHarness:
        """创建并启动 Browser；失败由 Harness 分类为环境或实现结果。"""

        harness = self.create(context, **kwargs)
        harness.start()
        return harness
