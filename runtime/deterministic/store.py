"""F14 派生 Runtime 数据的受控 SQLite 存储。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..errors import RuntimeStorageError, RuntimeValidationError
from ..session_store import SessionStore, stable_id, utc_now


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DerivedRuntimeStore:
    """只写入 F10 Session Store 的可重建派生表。"""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def write_artifact_snapshot(
        self,
        *,
        session_id: str,
        project_id: str,
        project_revision: int,
        policy_hash: str,
        records: Sequence[Mapping[str, Any]],
        snapshot_id: str | None = None,
    ) -> str:
        if project_revision < 0 or not project_id or not policy_hash:
            raise RuntimeValidationError("F14_ARTIFACT_SNAPSHOT_METADATA_INVALID")
        normalized = [dict(record) for record in records]
        normalized.sort(key=lambda item: str(item.get("artifact_id", "")))
        if any(not item.get("artifact_id") for item in normalized):
            raise RuntimeValidationError("F14_ARTIFACT_ID_MISSING")
        if len({str(item["artifact_id"]) for item in normalized}) != len(normalized):
            raise RuntimeValidationError("F14_ARTIFACT_ID_DUPLICATE")
        integrity_hash = hashlib.sha256(
            _canonical(normalized).encode("utf-8")
        ).hexdigest()
        resolved_snapshot_id = snapshot_id or stable_id(
            "f14-artifact-index",
            session_id,
            project_id,
            project_revision,
            policy_hash,
            integrity_hash,
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_artifact_index_snapshots WHERE snapshot_id=?",
                (resolved_snapshot_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_ARTIFACT_SNAPSHOT_ID_COLLISION")
                return resolved_snapshot_id
            connection.execute(
                """
                INSERT INTO f14_artifact_index_snapshots(
                    snapshot_id, session_id, project_id, project_revision,
                    policy_hash, status, record_count, integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, 'COMPLETE', ?, ?, ?)
                """,
                (
                    resolved_snapshot_id,
                    session_id,
                    project_id,
                    project_revision,
                    policy_hash,
                    len(normalized),
                    integrity_hash,
                    utc_now(),
                ),
            )
            for record in normalized:
                connection.execute(
                    """
                    INSERT INTO f14_artifact_records(
                        snapshot_id, artifact_id, kind, locator, content_hash,
                        project_revision, policy_hash, producer_role, authority,
                        approval_status, freshness, source_state_ref, record_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_snapshot_id,
                        str(record["artifact_id"]),
                        str(record["kind"]),
                        str(record["locator"]),
                        str(record["content_hash"]),
                        int(record["project_revision"]),
                        str(record["policy_hash"]),
                        str(record["producer_role"]),
                        str(record["authority"]),
                        str(record["approval_status"]),
                        str(record["freshness"]),
                        str(record["source_state_ref"]),
                        hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest(),
                    ),
                )
        return resolved_snapshot_id

    def read_artifact_snapshot(
        self, session_id: str, snapshot_id: str
    ) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            snapshot = connection.execute(
                """
                SELECT * FROM f14_artifact_index_snapshots
                WHERE session_id=? AND snapshot_id=?
                """,
                (session_id, snapshot_id),
            ).fetchone()
            if snapshot is None:
                raise RuntimeStorageError("F14_ARTIFACT_INDEX_MISSING")
            rows = connection.execute(
                """
                SELECT artifact_id, kind, locator, content_hash,
                       project_revision, policy_hash, producer_role, authority,
                       approval_status, freshness, source_state_ref
                FROM f14_artifact_records
                WHERE snapshot_id=?
                ORDER BY artifact_id
                """,
                (snapshot_id,),
            ).fetchall()
        finally:
            connection.close()
        records = [dict(row) for row in rows]
        if len(records) != int(snapshot["record_count"]):
            raise RuntimeStorageError("F14_ARTIFACT_INDEX_CORRUPT")
        actual_hash = hashlib.sha256(
            _canonical(records).encode("utf-8")
        ).hexdigest()
        if actual_hash != snapshot["integrity_hash"] or snapshot["status"] != "COMPLETE":
            raise RuntimeStorageError("F14_ARTIFACT_INDEX_CORRUPT")
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "session_id": snapshot["session_id"],
            "project_id": snapshot["project_id"],
            "project_revision": int(snapshot["project_revision"]),
            "policy_hash": snapshot["policy_hash"],
            "status": snapshot["status"],
            "record_count": len(records),
            "integrity_hash": actual_hash,
            "records": records,
        }

    def latest_artifact_snapshot(self, session_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                """
                SELECT snapshot_id FROM f14_artifact_index_snapshots
                WHERE session_id=?
                ORDER BY created_at DESC, snapshot_id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_ARTIFACT_INDEX_MISSING")
        return self.read_artifact_snapshot(session_id, str(row["snapshot_id"]))

    def write_dependency_snapshot(
        self,
        *,
        session_id: str,
        project_id: str,
        project_revision: int,
        policy_hash: str,
        edges: Sequence[Mapping[str, Any]],
        snapshot_id: str | None = None,
    ) -> str:
        if project_revision < 0 or not project_id or not policy_hash:
            raise RuntimeValidationError("F14_GRAPH_SNAPSHOT_METADATA_INVALID")
        normalized = [dict(edge) for edge in edges]
        normalized.sort(key=lambda item: str(item.get("edge_id", "")))
        if any(not item.get("edge_id") for item in normalized):
            raise RuntimeValidationError("F14_GRAPH_EDGE_ID_MISSING")
        if len({str(item["edge_id"]) for item in normalized}) != len(normalized):
            raise RuntimeValidationError("F14_GRAPH_EDGE_ID_DUPLICATE")
        integrity_hash = hashlib.sha256(
            _canonical(normalized).encode("utf-8")
        ).hexdigest()
        resolved_snapshot_id = snapshot_id or stable_id(
            "f14-dependency-graph",
            session_id,
            project_id,
            project_revision,
            policy_hash,
            integrity_hash,
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_dependency_graph_snapshots WHERE snapshot_id=?",
                (resolved_snapshot_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_GRAPH_SNAPSHOT_ID_COLLISION")
                return resolved_snapshot_id
            connection.execute(
                """
                INSERT INTO f14_dependency_graph_snapshots(
                    snapshot_id, session_id, project_id, project_revision,
                    policy_hash, status, edge_count, integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, 'COMPLETE', ?, ?, ?)
                """,
                (
                    resolved_snapshot_id,
                    session_id,
                    project_id,
                    project_revision,
                    policy_hash,
                    len(normalized),
                    integrity_hash,
                    utc_now(),
                ),
            )
            for edge in normalized:
                connection.execute(
                    """
                    INSERT INTO f14_dependency_edges(
                        snapshot_id, edge_id, graph_kind, edge_type, source,
                        target, evidence_ref, source_hash, revision, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resolved_snapshot_id,
                        str(edge["edge_id"]),
                        str(edge["graph_kind"]),
                        str(edge["edge_type"]),
                        str(edge["source"]),
                        str(edge["target"]),
                        str(edge["evidence_ref"]),
                        str(edge["source_hash"]),
                        int(edge["revision"]),
                        str(edge["confidence"]),
                    ),
                )
        return resolved_snapshot_id

    def read_dependency_snapshot(
        self, session_id: str, snapshot_id: str
    ) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            snapshot = connection.execute(
                """
                SELECT * FROM f14_dependency_graph_snapshots
                WHERE session_id=? AND snapshot_id=?
                """,
                (session_id, snapshot_id),
            ).fetchone()
            if snapshot is None:
                raise RuntimeStorageError("F14_GRAPH_SNAPSHOT_MISSING")
            rows = connection.execute(
                """
                SELECT edge_id, graph_kind, edge_type, source, target,
                       evidence_ref, source_hash, revision, confidence
                FROM f14_dependency_edges
                WHERE snapshot_id=?
                ORDER BY edge_id
                """,
                (snapshot_id,),
            ).fetchall()
        finally:
            connection.close()
        edges = [dict(row) for row in rows]
        if len(edges) != int(snapshot["edge_count"]):
            raise RuntimeStorageError("F14_GRAPH_SNAPSHOT_CORRUPT")
        actual_hash = hashlib.sha256(_canonical(edges).encode("utf-8")).hexdigest()
        if actual_hash != snapshot["integrity_hash"] or snapshot["status"] != "COMPLETE":
            raise RuntimeStorageError("F14_GRAPH_SNAPSHOT_CORRUPT")
        return {
            "snapshot_id": snapshot["snapshot_id"],
            "session_id": snapshot["session_id"],
            "project_id": snapshot["project_id"],
            "project_revision": int(snapshot["project_revision"]),
            "policy_hash": snapshot["policy_hash"],
            "status": snapshot["status"],
            "edge_count": len(edges),
            "integrity_hash": actual_hash,
            "edges": edges,
        }

    def write_diff_index(
        self,
        *,
        session_id: str,
        project_id: str,
        baseline_revision: int,
        current_revision: int,
        stale: bool,
        result: Mapping[str, Any],
        diff_id: str | None = None,
    ) -> str:
        if baseline_revision < 0 or current_revision < 0 or not project_id:
            raise RuntimeValidationError("F14_DIFF_REVISION_INVALID")
        normalized = dict(result)
        integrity_hash = hashlib.sha256(
            _canonical(normalized).encode("utf-8")
        ).hexdigest()
        resolved_diff_id = diff_id or stable_id(
            "f14-diff",
            session_id,
            project_id,
            baseline_revision,
            current_revision,
            integrity_hash,
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_diff_indexes WHERE diff_id=?",
                (resolved_diff_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_DIFF_ID_COLLISION")
                return resolved_diff_id
            connection.execute(
                """
                INSERT INTO f14_diff_indexes(
                    diff_id, session_id, project_id, baseline_revision,
                    current_revision, stale, result_json, integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_diff_id,
                    session_id,
                    project_id,
                    baseline_revision,
                    current_revision,
                    int(stale),
                    _canonical(normalized),
                    integrity_hash,
                    utc_now(),
                ),
            )
        return resolved_diff_id

    def read_diff_index(self, session_id: str, diff_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_diff_indexes WHERE session_id=? AND diff_id=?",
                (session_id, diff_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_DIFF_MISSING")
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_DIFF_CORRUPT") from exc
        if (
            not isinstance(result, dict)
            or hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest()
            != row["integrity_hash"]
        ):
            raise RuntimeStorageError("F14_DIFF_CORRUPT")
        return {
            "diff_id": row["diff_id"],
            "session_id": row["session_id"],
            "project_id": row["project_id"],
            "baseline_revision": int(row["baseline_revision"]),
            "current_revision": int(row["current_revision"]),
            "stale": bool(row["stale"]),
            "result": result,
            "integrity_hash": row["integrity_hash"],
        }

    def get_source_fingerprint(
        self, cache_key: str
    ) -> dict[str, Any] | None:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_source_fingerprints WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
        finally:
            connection.close()
        return dict(row) if row is not None else None

    def put_source_fingerprint(self, value: Mapping[str, Any]) -> None:
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO f14_source_fingerprints(
                    cache_key, canonical_locator, file_identity, file_size,
                    mtime_ns, filesystem_metadata_json, project_revision,
                    policy_hash, role_scope, parser_version,
                    known_content_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    canonical_locator=excluded.canonical_locator,
                    file_identity=excluded.file_identity,
                    file_size=excluded.file_size,
                    mtime_ns=excluded.mtime_ns,
                    filesystem_metadata_json=excluded.filesystem_metadata_json,
                    project_revision=excluded.project_revision,
                    policy_hash=excluded.policy_hash,
                    role_scope=excluded.role_scope,
                    parser_version=excluded.parser_version,
                    known_content_hash=excluded.known_content_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    str(value["cache_key"]),
                    str(value["canonical_locator"]),
                    str(value["file_identity"]),
                    int(value["file_size"]),
                    int(value["mtime_ns"]),
                    _canonical(value["filesystem_metadata"]),
                    int(value["project_revision"]),
                    str(value["policy_hash"]),
                    str(value["role_scope"]),
                    str(value["parser_version"]),
                    value.get("known_content_hash"),
                    utc_now(),
                ),
            )

    def get_content_cache(self, content_hash: str) -> dict[str, Any] | None:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_content_cache WHERE content_hash=?",
                (content_hash,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        blob = bytes(row["content_blob"])
        actual = hashlib.sha256(blob).hexdigest()
        if actual != content_hash or row["checksum"] != actual:
            raise RuntimeStorageError("F14_CONTENT_CACHE_CORRUPT")
        return {
            "content_hash": row["content_hash"],
            "content": blob,
            "content_size": int(row["content_size"]),
            "decoded_encoding": row["decoded_encoding"],
        }

    def put_content_cache(
        self, content_hash: str, content: bytes, *, decoded_encoding: str = "utf-8"
    ) -> None:
        actual = hashlib.sha256(content).hexdigest()
        if actual != content_hash:
            raise RuntimeValidationError("F14_CONTENT_HASH_MISMATCH")
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO f14_content_cache(
                    content_hash, content_blob, content_size, decoded_encoding,
                    checksum, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    content_blob=excluded.content_blob,
                    content_size=excluded.content_size,
                    decoded_encoding=excluded.decoded_encoding,
                    checksum=excluded.checksum,
                    updated_at=excluded.updated_at
                """,
                (
                    content_hash,
                    content,
                    len(content),
                    decoded_encoding,
                    actual,
                    utc_now(),
                    utc_now(),
                ),
            )

    def get_parsed_cache(
        self,
        content_hash: str,
        *,
        parser_version: str,
        policy_hash: str,
        role_scope: str,
    ) -> dict[str, Any] | None:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                """
                SELECT * FROM f14_parsed_cache
                WHERE content_hash=? AND parser_version=? AND policy_hash=? AND role_scope=?
                """,
                (content_hash, parser_version, policy_hash, role_scope),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        try:
            parsed = json.loads(row["parsed_json"])
            derived = json.loads(row["derived_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_PARSED_CACHE_CORRUPT") from exc
        payload = {"parsed": parsed, "derived": derived}
        actual = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        if actual != row["parsed_hash"]:
            raise RuntimeStorageError("F14_PARSED_CACHE_CORRUPT")
        return payload

    def put_parsed_cache(
        self,
        content_hash: str,
        *,
        parser_version: str,
        policy_hash: str,
        role_scope: str,
        parsed: Any,
        derived: Any,
    ) -> None:
        payload = {"parsed": parsed, "derived": derived}
        parsed_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        with self.store.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO f14_parsed_cache(
                    content_hash, parser_version, policy_hash, role_scope,
                    parsed_json, derived_json, parsed_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash, parser_version, policy_hash, role_scope)
                DO UPDATE SET
                    parsed_json=excluded.parsed_json,
                    derived_json=excluded.derived_json,
                    parsed_hash=excluded.parsed_hash,
                    updated_at=excluded.updated_at
                """,
                (
                    content_hash,
                    parser_version,
                    policy_hash,
                    role_scope,
                    _canonical(parsed),
                    _canonical(derived),
                    parsed_hash,
                    utc_now(),
                    utc_now(),
                ),
            )

    def write_test_result(
        self,
        *,
        session_id: str,
        parser_version: str,
        result: Mapping[str, Any],
        result_id: str | None = None,
    ) -> str:
        normalized = dict(result)
        if not parser_version:
            raise RuntimeValidationError("F14_TEST_PARSER_VERSION_MISSING")
        integrity_hash = hashlib.sha256(
            _canonical(normalized).encode("utf-8")
        ).hexdigest()
        resolved_result_id = result_id or stable_id(
            "f14-test-result",
            session_id,
            parser_version,
            integrity_hash,
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_test_results WHERE result_id=?",
                (resolved_result_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_TEST_RESULT_ID_COLLISION")
                return resolved_result_id
            connection.execute(
                """
                INSERT INTO f14_test_results(
                    result_id, session_id, parser_version, parse_status,
                    command, exit_code, duration_ms, source_locator,
                    raw_log_locator, confidence, result_json,
                    integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_result_id,
                    session_id,
                    parser_version,
                    str(normalized.get("parse_status", "UNKNOWN")),
                    str(normalized.get("command", "")),
                    normalized.get("exit_code"),
                    normalized.get("duration_ms"),
                    str(normalized.get("source_locator", "")),
                    str(normalized.get("raw_log_locator", "")),
                    str(normalized.get("confidence", "none")),
                    _canonical(normalized),
                    integrity_hash,
                    utc_now(),
                ),
            )
        return resolved_result_id

    def read_test_result(self, session_id: str, result_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_test_results WHERE session_id=? AND result_id=?",
                (session_id, result_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_TEST_RESULT_MISSING")
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_TEST_RESULT_CORRUPT") from exc
        actual_hash = hashlib.sha256(_canonical(result).encode("utf-8")).hexdigest()
        if actual_hash != row["integrity_hash"]:
            raise RuntimeStorageError("F14_TEST_RESULT_CORRUPT")
        return {
            "result_id": row["result_id"],
            "session_id": row["session_id"],
            "parser_version": row["parser_version"],
            "result": result,
            "integrity_hash": actual_hash,
        }

    @staticmethod
    def _json_record(value: Any) -> dict[str, Any]:
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        elif hasattr(value, "manifest"):
            value = value.manifest
        if not isinstance(value, Mapping):
            raise RuntimeValidationError("F14_CONTEXT_RECORD_INVALID")
        return dict(value)

    def write_context_semantic_snapshot(
        self,
        *,
        session_id: str,
        run_id: str,
        project_id: str,
        role: str,
        project_revision: int,
        semantic: Any,
        semantic_id: str | None = None,
    ) -> str:
        value = self._json_record(semantic)
        integrity_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        model_hash = str(value.get("model_hash", ""))
        resolved_id = semantic_id or stable_id(
            "f14-context-semantic", session_id, run_id, integrity_hash
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_context_semantic_snapshots WHERE semantic_id=?",
                (resolved_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_SEMANTIC_ID_COLLISION")
                return resolved_id
            connection.execute(
                """
                INSERT INTO f14_context_semantic_snapshots(
                    semantic_id, session_id, run_id, project_id, role,
                    project_revision, model_hash, semantic_json,
                    integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_id,
                    session_id,
                    run_id,
                    project_id,
                    role,
                    project_revision,
                    model_hash,
                    _canonical(value),
                    integrity_hash,
                    utc_now(),
                ),
            )
        return resolved_id

    def read_context_semantic_snapshot(self, session_id: str, semantic_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_context_semantic_snapshots WHERE session_id=? AND semantic_id=?",
                (session_id, semantic_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_SEMANTIC_MISSING")
        try:
            value = json.loads(row["semantic_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_SEMANTIC_CORRUPT") from exc
        actual_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        if actual_hash != row["integrity_hash"]:
            raise RuntimeStorageError("F14_SEMANTIC_CORRUPT")
        return {"semantic_id": semantic_id, "result": value, "integrity_hash": actual_hash}

    def write_shadow_comparison(
        self,
        *,
        session_id: str,
        run_id: str,
        project_id: str,
        role: str,
        project_revision: int,
        comparison: Any,
        comparison_id: str | None = None,
    ) -> str:
        value = self._json_record(comparison)
        integrity_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        resolved_id = comparison_id or stable_id(
            "f14-shadow-comparison", session_id, run_id, integrity_hash
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_shadow_comparisons WHERE comparison_id=?",
                (resolved_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_SHADOW_COMPARISON_ID_COLLISION")
                return resolved_id
            connection.execute(
                """
                INSERT INTO f14_shadow_comparisons(
                    comparison_id, session_id, run_id, project_id, role,
                    project_revision, comparison_json, integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_id,
                    session_id,
                    run_id,
                    project_id,
                    role,
                    project_revision,
                    _canonical(value),
                    integrity_hash,
                    utc_now(),
                ),
            )
        return resolved_id

    def write_shadow_gate_result(
        self,
        *,
        session_id: str,
        run_id: str,
        comparison_id: str,
        gate: Mapping[str, Any],
        gate_id: str | None = None,
    ) -> str:
        value = dict(gate)
        integrity_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        resolved_id = gate_id or stable_id("f14-shadow-gate", session_id, comparison_id, integrity_hash)
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_shadow_gate_results WHERE gate_id=?",
                (resolved_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_SHADOW_GATE_ID_COLLISION")
                return resolved_id
            connection.execute(
                """
                INSERT INTO f14_shadow_gate_results(
                    gate_id, session_id, run_id, comparison_id, result,
                    gate_json, integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_id,
                    session_id,
                    run_id,
                    comparison_id,
                    str(value.get("result", "UNKNOWN")),
                    _canonical(value),
                    integrity_hash,
                    utc_now(),
                ),
            )
        return resolved_id

    def write_selective_candidate(
        self,
        *,
        session_id: str,
        run_id: str,
        evaluation: Any,
        evaluation_id: str | None = None,
    ) -> str:
        value = self._json_record(evaluation)
        integrity_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        resolved_id = evaluation_id or stable_id(
            "f14-selective-candidate", session_id, run_id, integrity_hash
        )
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT integrity_hash FROM f14_selective_candidates WHERE evaluation_id=?",
                (resolved_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError("F14_SELECTIVE_ID_COLLISION")
                return resolved_id
            connection.execute(
                """
                INSERT INTO f14_selective_candidates(
                    evaluation_id, session_id, run_id, candidate_id,
                    result_json, integrity_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_id,
                    session_id,
                    run_id,
                    str(value.get("candidate_id", "")),
                    _canonical(value),
                    integrity_hash,
                    utc_now(),
                ),
            )
        return resolved_id

    def _write_escalation_record(
        self,
        *,
        table: str,
        id_column: str,
        record_id: str,
        session_id: str,
        foreign_column: str,
        foreign_value: str,
        status: str,
        json_column: str,
        value: Mapping[str, Any],
        collision_code: str,
        insert_columns: str,
        insert_values: tuple[Any, ...],
    ) -> str:
        """写入 F14-D 追加式证据，统一执行幂等和完整性校验。"""

        normalized = dict(value)
        integrity_hash = hashlib.sha256(
            _canonical(normalized).encode("utf-8")
        ).hexdigest()
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                f"SELECT integrity_hash FROM {table} WHERE {id_column}=?",
                (record_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError(collision_code)
                return record_id
            connection.execute(
                f"""
                INSERT INTO {table}({insert_columns}, integrity_hash, created_at)
                VALUES ({', '.join('?' for _ in insert_values)}, ?, ?)
                """,
                (
                    *insert_values,
                    integrity_hash,
                    utc_now(),
                ),
            )
        return record_id

    def write_context_request(
        self,
        *,
        session_id: str,
        request_id: str,
        request: Mapping[str, Any],
        status: str = "REQUESTED",
    ) -> str:
        value = dict(request)
        return self._write_escalation_record(
            table="f14_context_requests",
            id_column="request_id",
            record_id=request_id,
            session_id=session_id,
            foreign_column="run_id",
            foreign_value=str(value.get("run_id", "")),
            status=status,
            json_column="request_json",
            value=value,
            collision_code="F14_CONTEXT_REQUEST_ID_COLLISION",
            insert_columns="request_id, session_id, run_id, role, project_revision, requested_level, status, request_json",
            insert_values=(
                request_id,
                session_id,
                str(value.get("run_id", "")),
                str(value.get("role", "")),
                int(value.get("project_revision", 0)),
                str(value.get("requested_level", "")),
                status,
                _canonical(value),
            ),
        )

    def write_context_authorization(
        self,
        *,
        session_id: str,
        request_id: str,
        authorization_id: str,
        authorization: Mapping[str, Any],
        decision: str,
    ) -> str:
        value = dict(authorization)
        return self._write_escalation_record(
            table="f14_context_authorizations",
            id_column="authorization_id",
            record_id=authorization_id,
            session_id=session_id,
            foreign_column="request_id",
            foreign_value=request_id,
            status=decision,
            json_column="authorization_json",
            value=value,
            collision_code="F14_CONTEXT_AUTHORIZATION_ID_COLLISION",
            insert_columns="authorization_id, request_id, session_id, decision, authorization_json",
            insert_values=(
                authorization_id,
                request_id,
                session_id,
                decision,
                _canonical(value),
            ),
        )

    def write_context_expansion(
        self,
        *,
        session_id: str,
        request_id: str,
        expansion_id: str,
        expansion: Mapping[str, Any],
        status: str,
    ) -> str:
        value = dict(expansion)
        return self._write_escalation_record(
            table="f14_context_expansions",
            id_column="expansion_id",
            record_id=expansion_id,
            session_id=session_id,
            foreign_column="request_id",
            foreign_value=request_id,
            status=status,
            json_column="expansion_json",
            value=value,
            collision_code="F14_CONTEXT_EXPANSION_ID_COLLISION",
            insert_columns="expansion_id, request_id, session_id, status, expansion_json",
            insert_values=(
                expansion_id,
                request_id,
                session_id,
                status,
                _canonical(value),
            ),
        )

    def write_context_recovery(
        self,
        *,
        session_id: str,
        request_id: str,
        recovery_id: str,
        recovery: Mapping[str, Any],
        status: str,
    ) -> str:
        value = dict(recovery)
        return self._write_escalation_record(
            table="f14_context_recoveries",
            id_column="recovery_id",
            record_id=recovery_id,
            session_id=session_id,
            foreign_column="request_id",
            foreign_value=request_id,
            status=status,
            json_column="recovery_json",
            value=value,
            collision_code="F14_CONTEXT_RECOVERY_ID_COLLISION",
            insert_columns="recovery_id, request_id, session_id, status, recovery_json",
            insert_values=(
                recovery_id,
                request_id,
                session_id,
                status,
                _canonical(value),
            ),
        )

    def _write_incremental_record(
        self,
        *,
        table: str,
        id_column: str,
        record_id: str,
        session_id: str,
        project_id: str,
        status: str,
        value: Mapping[str, Any],
        insert_columns: str,
        insert_values: tuple[Any, ...],
        collision_code: str,
    ) -> str:
        """以单事务写入增量派生记录，事务失败时不留下可信半成品。"""

        normalized = dict(value)
        integrity_hash = hashlib.sha256(_canonical(normalized).encode("utf-8")).hexdigest()
        with self.store.transaction(immediate=True) as connection:
            existing = connection.execute(
                f"SELECT integrity_hash FROM {table} WHERE {id_column}=?",
                (record_id,),
            ).fetchone()
            if existing is not None:
                if existing["integrity_hash"] != integrity_hash:
                    raise RuntimeStorageError(collision_code)
                return record_id
            connection.execute(
                f"""
                INSERT INTO {table}({insert_columns}, integrity_hash, created_at)
                VALUES ({', '.join('?' for _ in insert_values)}, ?, ?)
                """,
                (*insert_values, integrity_hash, utc_now()),
            )
        return record_id

    def write_incremental_manifest(
        self,
        *,
        session_id: str,
        project_id: str,
        manifest: Any,
        status: str = "COMPLETE",
        manifest_id: str | None = None,
    ) -> str:
        value = self._json_record(manifest)
        resolved_id = manifest_id or stable_id(
            "f14-incremental-manifest", session_id, value.get("manifest_hash", ""), status
        )
        return self._write_incremental_record(
            table="f14_incremental_manifests",
            id_column="manifest_id",
            record_id=resolved_id,
            session_id=session_id,
            project_id=project_id,
            status=status,
            value=value,
            collision_code="F14_INCREMENTAL_MANIFEST_ID_COLLISION",
            insert_columns=(
                "manifest_id, session_id, project_id, role, task_identity, "
                "project_revision, policy_hash, parser_version, summary_version, "
                "status, manifest_json"
            ),
            insert_values=(
                resolved_id,
                session_id,
                project_id,
                str(value.get("role", "")),
                str(value.get("task_identity", "")),
                int(value.get("project_revision", -1)),
                str(value.get("policy_hash", "")),
                str(value.get("parser_version", "")),
                str(value.get("summary_version", "")),
                status,
                _canonical(value),
            ),
        )

    def read_incremental_manifest(
        self, session_id: str, manifest_id: str
    ) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_incremental_manifests WHERE session_id=? AND manifest_id=?",
                (session_id, manifest_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_INCREMENTAL_MANIFEST_MISSING")
        if row["status"] != "COMPLETE":
            raise RuntimeStorageError("F14_INCREMENTAL_MANIFEST_UNTRUSTED")
        try:
            value = json.loads(row["manifest_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_INCREMENTAL_MANIFEST_CORRUPT") from exc
        actual_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        if actual_hash != row["integrity_hash"]:
            raise RuntimeStorageError("F14_INCREMENTAL_MANIFEST_CORRUPT")
        return {
            "manifest_id": manifest_id,
            "session_id": session_id,
            "project_id": row["project_id"],
            "status": row["status"],
            "manifest": value,
            "integrity_hash": actual_hash,
        }

    def latest_incremental_manifest(self, session_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                """
                SELECT manifest_id FROM f14_incremental_manifests
                WHERE session_id=? AND status='COMPLETE'
                ORDER BY created_at DESC, manifest_id DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_INCREMENTAL_MANIFEST_MISSING")
        return self.read_incremental_manifest(session_id, str(row["manifest_id"]))

    def write_context_delta(
        self,
        *,
        session_id: str,
        project_id: str,
        delta: Any,
        status: str = "COMPLETE",
        delta_id: str | None = None,
    ) -> str:
        value = self._json_record(delta)
        resolved_id = delta_id or stable_id(
            "f14-context-delta", session_id, value.get("base_manifest_hash"), value.get("current_revision"), _canonical(value)
        )
        return self._write_incremental_record(
            table="f14_context_deltas",
            id_column="delta_id",
            record_id=resolved_id,
            session_id=session_id,
            project_id=project_id,
            status=status,
            value=value,
            collision_code="F14_CONTEXT_DELTA_ID_COLLISION",
            insert_columns="delta_id, session_id, project_id, base_manifest_hash, current_revision, status, delta_json",
            insert_values=(
                resolved_id,
                session_id,
                project_id,
                value.get("base_manifest_hash"),
                int(value.get("current_revision", -1)),
                status,
                _canonical(value),
            ),
        )

    def read_context_delta(self, session_id: str, delta_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_context_deltas WHERE session_id=? AND delta_id=?",
                (session_id, delta_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_CONTEXT_DELTA_MISSING")
        if row["status"] != "COMPLETE":
            raise RuntimeStorageError("F14_CONTEXT_DELTA_UNTRUSTED")
        try:
            value = json.loads(row["delta_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_CONTEXT_DELTA_CORRUPT") from exc
        actual_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        if actual_hash != row["integrity_hash"]:
            raise RuntimeStorageError("F14_CONTEXT_DELTA_CORRUPT")
        return {"delta_id": delta_id, "session_id": session_id, "delta": value, "integrity_hash": actual_hash}

    def write_canonical_summary(
        self,
        *,
        session_id: str,
        project_id: str,
        summary: Any,
        status: str = "COMPLETE",
    ) -> str:
        value = self._json_record(summary)
        summary_id = str(value.get("summary_id", ""))
        if not summary_id:
            raise RuntimeValidationError("F14_SUMMARY_ID_MISSING")
        return self._write_incremental_record(
            table="f14_canonical_summaries",
            id_column="summary_id",
            record_id=summary_id,
            session_id=session_id,
            project_id=project_id,
            status=status,
            value=value,
            collision_code="F14_SUMMARY_ID_COLLISION",
            insert_columns="summary_id, session_id, project_id, role, project_revision, policy_hash, summary_type, authority_level, status, summary_json",
            insert_values=(
                summary_id,
                session_id,
                project_id,
                str(value.get("role_scope", "")),
                int(value.get("project_revision", -1)),
                str(value.get("policy_hash", "")),
                str(value.get("summary_type", "")),
                str(value.get("authority_level", "")),
                status,
                _canonical(value),
            ),
        )

    def read_canonical_summary(self, session_id: str, summary_id: str) -> dict[str, Any]:
        connection = self.store.raw_connection()
        try:
            row = connection.execute(
                "SELECT * FROM f14_canonical_summaries WHERE session_id=? AND summary_id=?",
                (session_id, summary_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeStorageError("F14_SUMMARY_MISSING")
        if row["status"] != "COMPLETE":
            raise RuntimeStorageError("F14_SUMMARY_UNTRUSTED")
        try:
            value = json.loads(row["summary_json"])
        except json.JSONDecodeError as exc:
            raise RuntimeStorageError("F14_SUMMARY_CORRUPT") from exc
        actual_hash = hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
        if actual_hash != row["integrity_hash"]:
            raise RuntimeStorageError("F14_SUMMARY_CORRUPT")
        return {"summary_id": summary_id, "session_id": session_id, "summary": value, "integrity_hash": actual_hash}


__all__ = ["DerivedRuntimeStore"]
