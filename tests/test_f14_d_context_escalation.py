"""F14-D Shadow/Controlled Context Escalation 专项测试。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from runtime.context import (
    ContextBuildRequest,
    ContextBuilder,
    ContextEscalationRuntime,
    ContextRequest,
)
from runtime.context.policy import ContextPolicy
from runtime.deterministic.artifact_index import ArtifactIndexBuilder
from runtime.deterministic.dependency_graph import DependencyEdge, DependencyGraph
from runtime.deterministic.store import DerivedRuntimeStore
from runtime.errors import RuntimeValidationError
from tests.test_formal_context_builder import _prepare_project


class F14DContextEscalationTests(unittest.TestCase):
    """验证请求、授权、分层交付、Expansion Loop 和 F13 恢复。"""

    def _fixture(self, *, authority: str = "RUNTIME_EVIDENCE", status: str = "VERIFIED"):
        directory = tempfile.TemporaryDirectory(prefix="test_f14_d_escalation_")
        root = Path(directory.name)
        store, session_id, run_id, project_root = _prepare_project(
            root, "IMPLEMENTING", "generator"
        )
        source = project_root / "code/escalated.py"
        source.write_text("def safe_value():\n    return 'safe'\n", encoding="utf-8")
        second = project_root / "code/dependency.py"
        second.write_text("DEPENDENCY = 'safe'\n", encoding="utf-8")
        policy_hash = ContextPolicy().policy_hash
        builder = ArtifactIndexBuilder(
            project_root,
            project_revision=0,
            policy_hash=policy_hash,
            producer_role="generator",
        )
        records = builder.build(
            [
                {
                    "artifact_id": "A-ESCALATED",
                    "kind": "code",
                    "locator": "code/escalated.py",
                    "authority": authority,
                    "approval_status": status,
                    "source_state_ref": "runtime:test-evidence"
                    if authority == "RUNTIME_EVIDENCE"
                    else "",
                },
                {
                    "artifact_id": "B-DEPENDENCY",
                    "kind": "code",
                    "locator": "code/dependency.py",
                    "authority": authority,
                    "approval_status": status,
                    "source_state_ref": "runtime:test-dependency"
                    if authority == "RUNTIME_EVIDENCE"
                    else "",
                },
            ]
        )
        artifact_snapshot_id = builder.persist(
            store,
            session_id=session_id,
            project_id=store.get_session(session_id).project_id,
            records=records,
        )
        edge = DependencyEdge(
            graph_kind="execution",
            edge_type="implements",
            source="A-ESCALATED",
            target="B-DEPENDENCY",
            evidence_ref="runtime:test-edge",
            source_hash="a" * 64,
            revision=0,
            confidence="explicit",
        )
        graph = DependencyGraph([edge])
        dependency_snapshot_id = graph.persist(
            store,
            session_id=session_id,
            project_id=store.get_session(session_id).project_id,
            project_revision=0,
            policy_hash=policy_hash,
        )
        current = ContextBuilder(store).build(
            ContextBuildRequest(
                session_id,
                run_id,
                "generator",
                ("code/main.py",),
            )
        )
        runtime = ContextEscalationRuntime(store, policy_hash=policy_hash)
        return (
            directory,
            store,
            session_id,
            run_id,
            current,
            runtime,
            artifact_snapshot_id,
            dependency_snapshot_id,
        )

    def _request(
        self,
        current,
        session_id,
        run_id,
        artifact_snapshot_id,
        dependency_snapshot_id,
        *,
        request_id="request-1",
        level="L2",
        ref="A-ESCALATED",
        count=0,
        budget=4096,
    ):
        return ContextRequest(
            request_id=request_id,
            session_id=session_id,
            run_id=run_id,
            role="generator",
            project_revision=current.project_revision,
            current_context_hash=current.context_hash,
            missing_dependency_refs=(ref,),
            desired_source_kinds=("code",),
            requested_level=level,
            reason="缺少受影响代码证据",
            requested_budget_bytes=budget,
            expansion_count=count,
            artifact_snapshot_id=artifact_snapshot_id,
            dependency_snapshot_id=dependency_snapshot_id,
        )

    def test_l1_l2_l3_are_progressive_and_shadow_only(self) -> None:
        fixture = self._fixture()
        directory, store, session_id, run_id, current, runtime, artifact_id, graph_id = fixture
        try:
            l1 = self._request(current, session_id, run_id, artifact_id, graph_id, level="L1")
            auth_l1 = runtime.authorize(l1)
            self.assertEqual(auth_l1.decision, "APPROVED")
            expansion_l1 = runtime.deliver(l1, auth_l1, current, total_delivered_bytes=0)
            self.assertEqual(expansion_l1.status, "DELIVERED")
            self.assertIsNone(expansion_l1.sources[0].content)
            self.assertEqual(expansion_l1.sources[0].delivery_mode, "REFERENCE")

            l2 = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-2", level="L2",
            )
            result = runtime.run_expansion_loop(current, (l2,))
            self.assertEqual(result.status, "COMPLETED")
            self.assertIs(result.formal_context, current)
            self.assertEqual(result.llm_invocations, 0)
            self.assertIsNotNone(result.expansions[0].sources[0].content)
            self.assertIsNone(result.recovery)

            l3 = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-3", level="L3",
            )
            auth_l3 = runtime.authorize(l3)
            self.assertEqual(auth_l3.decision, "APPROVED")
            expansion_l3 = runtime.deliver(l3, auth_l3, current)
            self.assertEqual(expansion_l3.status, "DELIVERED")
            self.assertIsNotNone(expansion_l3.sources[0].content)
        finally:
            directory.cleanup()

    def test_runtime_only_resolves_indexed_ids_and_rejects_unknown_dependency(self) -> None:
        with self.assertRaisesRegex(RuntimeValidationError, "DEPENDENCY_REF_INVALID"):
            ContextRequest(
                request_id="request-path",
                session_id="session",
                run_id="run",
                role="generator",
                project_revision=0,
                current_context_hash="a" * 64,
                missing_dependency_refs=("code/secret.py",),
                desired_source_kinds=("code",),
                requested_level="L2",
                reason="path must not be accepted",
            )

        fixture = self._fixture()
        directory, store, session_id, run_id, current, runtime, artifact_id, graph_id = fixture
        try:
            request = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                ref="not-indexed",
            )
            authorization = runtime.authorize(request)
            self.assertEqual(authorization.decision, "DENIED")
            self.assertIn("ARTIFACT_NOT_INDEXED", authorization.reasons)
        finally:
            directory.cleanup()

    def test_unavailable_index_falls_back_to_f13_without_model_call(self) -> None:
        fixture = self._fixture()
        directory, store, session_id, run_id, current, runtime, _, _ = fixture
        try:
            request = self._request(
                current, session_id, run_id, "", "", request_id="request-unavailable"
            )
            result = runtime.run_expansion_loop(current, (request,))
            self.assertEqual(result.status, "FALLBACK_F13")
            self.assertIs(result.formal_context, current)
            self.assertEqual(result.llm_invocations, 0)
            self.assertIsNotNone(result.recovery)
            self.assertEqual(result.recovery.status, "F13_FALLBACK")
            self.assertTrue(result.recovery.preserved_formal_context)
            self.assertEqual(result.expansions[0].status, "FALLBACK_F13")
        finally:
            directory.cleanup()

    def test_stale_revision_role_path_secret_budget_and_l3_authority_fail_closed(self) -> None:
        fixture = self._fixture(authority="GENERATED_ARTIFACT", status="GENERATED")
        directory, store, session_id, run_id, current, runtime, artifact_id, graph_id = fixture
        try:
            stale = ContextRequest(
                **{
                    **self._request(current, session_id, run_id, artifact_id, graph_id).to_dict(),
                    "missing_dependency_refs": ("A-ESCALATED",),
                    "desired_source_kinds": ("code",),
                    "requested_level": "L2",
                    "project_revision": current.project_revision + 1,
                    "artifact_snapshot_id": artifact_id,
                    "dependency_snapshot_id": graph_id,
                }
            )
            stale_auth = runtime.authorize(stale)
            self.assertEqual(stale_auth.decision, "DENIED")
            self.assertIn("STALE_REVISION", stale_auth.reasons)

            l3 = self._request(
                current, session_id, run_id, artifact_id, graph_id, level="L3"
            )
            l3 = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-l3", level="L3",
            )
            l3_auth = runtime.authorize(l3)
            self.assertEqual(l3_auth.decision, "DENIED")
            self.assertIn("L3_AUTHORITY_REQUIRES_VERIFIED_SOURCE", l3_auth.reasons)

            low_budget = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-budget", budget=1,
            )
            low_auth = runtime.authorize(low_budget)
            low_expansion = runtime.deliver(low_budget, low_auth, current)
            self.assertEqual(low_expansion.status, "DENIED")
            self.assertIn("EXPANSION_BUDGET_EXCEEDED", low_expansion.reasons)
        finally:
            directory.cleanup()

    def test_unknown_dependency_and_secret_are_rejected_without_silent_omission(self) -> None:
        fixture = self._fixture()
        directory, store, session_id, run_id, current, runtime, artifact_id, graph_id = fixture
        try:
            policy_hash = ContextPolicy().policy_hash
            unknown_graph = DependencyGraph(
                [
                    DependencyEdge(
                        graph_kind="execution",
                        edge_type="implements",
                        source="A-ESCALATED",
                        target="B-DEPENDENCY",
                        evidence_ref="",
                        source_hash="",
                        revision=0,
                        confidence="unknown",
                    )
                ]
            )
            unknown_graph_id = unknown_graph.persist(
                store,
                session_id=session_id,
                project_id=store.get_session(session_id).project_id,
                project_revision=0,
                policy_hash=policy_hash,
            )
            unknown_request = self._request(
                current,
                session_id,
                run_id,
                artifact_id,
                unknown_graph_id,
                request_id="request-unknown-edge",
            )
            unknown_auth = runtime.authorize(unknown_request)
            self.assertEqual(unknown_auth.decision, "DENIED")
            self.assertIn("UNKNOWN_DEPENDENCY", unknown_auth.reasons)

            root = Path(store.get_session(session_id).project_root)
            secret = root / "code/secret_source.py"
            secret.write_text("API_KEY=must-not-leak\n", encoding="utf-8")
            builder = ArtifactIndexBuilder(
                root,
                project_revision=0,
                policy_hash=policy_hash,
                producer_role="generator",
            )
            records = builder.build(
                [
                    {
                        "artifact_id": "S-SECRET",
                        "kind": "code",
                        "locator": "code/secret_source.py",
                        "authority": "RUNTIME_EVIDENCE",
                        "approval_status": "VERIFIED",
                        "source_state_ref": "runtime:test-secret",
                    },
                    {
                        "artifact_id": "A-ESCALATED",
                        "kind": "code",
                        "locator": "code/escalated.py",
                        "authority": "RUNTIME_EVIDENCE",
                        "approval_status": "VERIFIED",
                        "source_state_ref": "runtime:test-evidence",
                    },
                ]
            )
            secret_artifact_id = builder.persist(
                store,
                session_id=session_id,
                project_id=store.get_session(session_id).project_id,
                records=records,
            )
            secret_graph = DependencyGraph(
                [
                    DependencyEdge(
                        graph_kind="execution",
                        edge_type="implements",
                        source="S-SECRET",
                        target="A-ESCALATED",
                        evidence_ref="runtime:test-secret-edge",
                        source_hash="b" * 64,
                        revision=0,
                        confidence="explicit",
                    )
                ]
            )
            secret_graph_id = secret_graph.persist(
                store,
                session_id=session_id,
                project_id=store.get_session(session_id).project_id,
                project_revision=0,
                policy_hash=policy_hash,
            )
            secret_request = self._request(
                current,
                session_id,
                run_id,
                secret_artifact_id,
                secret_graph_id,
                request_id="request-secret",
                ref="S-SECRET",
            )
            secret_auth = runtime.authorize(secret_request)
            self.assertEqual(secret_auth.decision, "APPROVED")
            secret_expansion = runtime.deliver(secret_request, secret_auth, current)
            self.assertEqual(secret_expansion.status, "DENIED")
            self.assertIn("CONTEXT_SECRET_FORBIDDEN", secret_expansion.reasons)
        finally:
            directory.cleanup()

    def test_expansion_loop_cycle_recovers_to_original_f13_context(self) -> None:
        fixture = self._fixture()
        directory, store, session_id, run_id, current, runtime, artifact_id, graph_id = fixture
        try:
            first = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-cycle-1", count=0,
            )
            second = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-cycle-2", count=1,
            )
            result = runtime.run_expansion_loop(current, (first, second))
            self.assertEqual(result.status, "FALLBACK_F13")
            self.assertIs(result.formal_context, current)
            self.assertIsNotNone(result.recovery)
            self.assertEqual(result.recovery.reason, "EXPANSION_CYCLE")
            self.assertEqual(result.llm_invocations, 0)
        finally:
            directory.cleanup()

    def test_append_only_audit_records_and_recovery_preserve_formal_context(self) -> None:
        fixture = self._fixture()
        directory, store, session_id, run_id, current, runtime, artifact_id, graph_id = fixture
        try:
            request = self._request(
                current, session_id, run_id, artifact_id, graph_id,
                request_id="request-audit",
            )
            result = runtime.run_expansion_loop(current, (request,))
            self.assertEqual(result.status, "COMPLETED")
            connection = store.raw_connection()
            try:
                counts = {
                    name: connection.execute(
                        f"SELECT COUNT(*) AS count FROM {name}"
                    ).fetchone()["count"]
                    for name in (
                        "f14_context_requests",
                        "f14_context_authorizations",
                        "f14_context_expansions",
                        "f14_context_recoveries",
                    )
                }
            finally:
                connection.close()
            self.assertEqual(counts["f14_context_requests"], 1)
            self.assertEqual(counts["f14_context_authorizations"], 1)
            self.assertEqual(counts["f14_context_expansions"], 1)
            self.assertEqual(counts["f14_context_recoveries"], 0)
            event_types = {event.event_type for event in store.list_events(session_id)}
            self.assertIn("CONTEXT_REQUESTED", event_types)
            self.assertIn("CONTEXT_AUTHORIZED", event_types)
            self.assertIn("CONTEXT_EXPANDED", event_types)
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
