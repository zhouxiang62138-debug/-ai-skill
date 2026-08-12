import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.deterministic.source_cache import SourceCache
from runtime.deterministic.telemetry import RuntimeTelemetry
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from tests.runtime_test_support import make_store


class F14SourceCacheTests(unittest.TestCase):
    def test_layer_one_fast_hit_avoids_read_but_is_not_trusted_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_cache_") as directory:
            root = Path(directory)
            (root / "source.txt").write_text("stable", encoding="utf-8")
            store, _session_id = make_store(directory)
            telemetry = RuntimeTelemetry()
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
                store=store,
                telemetry=telemetry,
            )
            first = cache.read("source.txt")
            second = cache.read("source.txt", require_trusted_hash=False)
            self.assertTrue(first.trusted)
            self.assertFalse(second.trusted)
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertEqual(1, telemetry.to_dict()["runtime_efficiency"]["file_reads"])
            self.assertEqual("hit", telemetry.to_dict()["cache_events"][-1]["hit_or_miss"])

    def test_protected_source_and_metadata_change_rehash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_cache_rehash_") as directory:
            root = Path(directory)
            source = root / "approved.yaml"
            source.write_text("approved: one\n", encoding="utf-8")
            telemetry = RuntimeTelemetry()
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="generator",
                parser_version="parser-v1",
                telemetry=telemetry,
            )
            first = cache.read("approved.yaml", approved=True)
            source.write_text("approved: two\n", encoding="utf-8")
            second = cache.read("approved.yaml", approved=True)
            self.assertNotEqual(first.content_hash, second.content_hash)
            self.assertEqual(2, telemetry.to_dict()["runtime_efficiency"]["file_reads"])
            self.assertTrue(second.trusted)

    def test_corrupt_content_cache_falls_back_to_reread(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_cache_corrupt_") as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("cache me", encoding="utf-8")
            store, _session_id = make_store(directory)
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
                store=store,
            )
            first = cache.read("source.txt")
            connection = store.raw_connection()
            try:
                connection.execute(
                    "UPDATE f14_content_cache SET content_blob=? WHERE content_hash=?",
                    (b"corrupted", first.content_hash),
                )
                connection.commit()
            finally:
                connection.close()
            restarted_telemetry = RuntimeTelemetry()
            restarted_cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
                store=store,
                telemetry=restarted_telemetry,
            )
            second = restarted_cache.read("source.txt", require_trusted_hash=False)
            self.assertEqual(hashlib.sha256(b"cache me").hexdigest(), second.content_hash)
            self.assertEqual(1, restarted_telemetry.to_dict()["runtime_efficiency"]["file_reads"])
            self.assertTrue(
                any(
                    event["invalidation_reason"] == "content_cache_corrupt"
                    for event in restarted_telemetry.to_dict()["cache_events"]
                )
            )

    def test_role_or_policy_change_does_not_reuse_fingerprint_and_path_is_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_cache_scope_") as directory:
            root = Path(directory)
            (root / "source.txt").write_text("scope", encoding="utf-8")
            store, _session_id = make_store(directory)
            first = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
                store=store,
            )
            first.read("source.txt")
            second_telemetry = RuntimeTelemetry()
            second = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v2",
                role_scope="generator",
                parser_version="parser-v1",
                store=store,
                telemetry=second_telemetry,
            )
            result = second.read("source.txt", require_trusted_hash=False)
            self.assertTrue(result.trusted)
            self.assertEqual(1, second_telemetry.to_dict()["runtime_efficiency"]["file_reads"])
            with self.assertRaisesRegex(RuntimeValidationError, "PATH_ANOMALY"):
                second.read("../outside.txt")

    def test_missing_source_and_secret_are_not_cached(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_cache_negative_") as directory:
            root = Path(directory)
            (root / "secret.txt").write_text("api_key: do-not-store\n", encoding="utf-8")
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
            )
            with self.assertRaisesRegex(RuntimeValidationError, "SECRET_FORBIDDEN"):
                cache.read("secret.txt")
            with self.assertRaisesRegex(RuntimeValidationError, "LOCATOR_STALE"):
                cache.read("missing.txt")

    def test_fingerprint_hit_still_runs_secret_policy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_cache_secret_hit_") as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text("safe", encoding="utf-8")
            cache = SourceCache(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                role_scope="planner",
                parser_version="parser-v1",
            )
            first = cache.read("source.txt")
            with patch.object(
                cache, "_get_content", return_value=b"api_key: injected"
            ):
                with self.assertRaisesRegex(RuntimeValidationError, "SECRET_FORBIDDEN"):
                    cache.read("source.txt", require_trusted_hash=False)


if __name__ == "__main__":
    unittest.main()
