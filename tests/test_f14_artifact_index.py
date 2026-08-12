import hashlib
import tempfile
import unittest
from pathlib import Path

from runtime.deterministic.artifact_index import ArtifactIndexBuilder
from runtime.deterministic.store import DerivedRuntimeStore
from runtime.errors import RuntimeStorageError, RuntimeValidationError
from tests.runtime_test_support import make_runtime_project, make_store, open_runtime_store


class F14ArtifactIndexTests(unittest.TestCase):
    def test_explicit_artifacts_are_hashed_and_persisted_as_rebuildable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_artifact_") as directory:
            root, session_id = make_runtime_project(directory)
            store = open_runtime_store(root)
            source = root / "memory/requirements/requirements_v001.yaml"
            source.write_text("requirements: []\n", encoding="utf-8")
            builder = ArtifactIndexBuilder(
                root,
                project_revision=0,
                policy_hash="policy-v1",
                producer_role="planner",
            )
            records = builder.build(
                [
                    {
                        "artifact_id": "REQ-SOURCE",
                        "kind": "requirements",
                        "locator": "memory/requirements/requirements_v001.yaml",
                        "authority": "APPROVED_REQUIREMENT",
                        "approval_status": "APPROVED",
                        "source_state_ref": "active_requirements",
                    }
                ]
            )
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                records[0].content_hash,
            )
            snapshot_id = builder.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
                records=records,
            )
            loaded = DerivedRuntimeStore(store).read_artifact_snapshot(
                session_id, snapshot_id
            )
            self.assertEqual("COMPLETE", loaded["status"])
            self.assertEqual("APPROVED_REQUIREMENT", loaded["records"][0]["authority"])
            self.assertEqual(
                "memory/requirements/requirements_v001.yaml",
                loaded["records"][0]["locator"],
            )

    def test_index_rejects_escape_missing_source_and_invalid_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_artifact_negative_") as directory:
            root = Path(directory)
            (root / "ok.txt").write_text("ok", encoding="utf-8")
            builder = ArtifactIndexBuilder(
                root,
                project_revision=0,
                policy_hash="policy-v1",
                producer_role="planner",
            )
            with self.assertRaisesRegex(RuntimeValidationError, "LOCATOR_ESCAPE"):
                builder.index_file(
                    artifact_id="escape",
                    kind="file",
                    locator="../outside.txt",
                    authority="UNKNOWN",
                    approval_status="UNKNOWN",
                )
            with self.assertRaisesRegex(RuntimeValidationError, "SOURCE_MISSING"):
                builder.index_file(
                    artifact_id="missing",
                    kind="file",
                    locator="missing.txt",
                    authority="UNKNOWN",
                    approval_status="UNKNOWN",
                )
            with self.assertRaisesRegex(RuntimeValidationError, "AUTHORITY_INVALID"):
                builder.index_file(
                    artifact_id="bad-authority",
                    kind="file",
                    locator="ok.txt",
                    authority="MODEL_GUESS",
                    approval_status="UNKNOWN",
                )

    def test_snapshot_is_idempotent_and_corruption_blocks_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_f14_artifact_corrupt_") as directory:
            root = Path(directory)
            (root / "ok.txt").write_text("ok", encoding="utf-8")
            store, session_id = make_store(directory)
            builder = ArtifactIndexBuilder(
                root,
                project_revision=1,
                policy_hash="policy-v1",
                producer_role="planner",
            )
            records = builder.build(
                [
                    {
                        "artifact_id": "A-1",
                        "kind": "file",
                        "locator": "ok.txt",
                        "authority": "GENERATED_ARTIFACT",
                        "approval_status": "UNKNOWN",
                    }
                ]
            )
            snapshot_id = builder.persist(
                store,
                session_id=session_id,
                project_id="test_runtime",
                records=records,
                snapshot_id="snapshot-fixed",
            )
            self.assertEqual(
                snapshot_id,
                builder.persist(
                    store,
                    session_id=session_id,
                    project_id="test_runtime",
                    records=records,
                    snapshot_id="snapshot-fixed",
                ),
            )
            connection = store.raw_connection()
            try:
                connection.execute(
                    "DROP TRIGGER f14_artifact_records_no_update"
                )
                connection.execute(
                    "UPDATE f14_artifact_records SET content_hash=? WHERE snapshot_id=?",
                    ("0" * 64, snapshot_id),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeStorageError, "INDEX_CORRUPT"):
                DerivedRuntimeStore(store).read_artifact_snapshot(
                    session_id, snapshot_id
                )


if __name__ == "__main__":
    unittest.main()
