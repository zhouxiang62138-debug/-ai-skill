"""协议漂移回归：任何跨 Authority 的不一致都必须 fail closed。"""

from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.protocol_consistency import (
    ROOT,
    check_documents,
    load_document_texts,
    load_documents,
    run_check,
)


class ProtocolConsistencyTests(unittest.TestCase):
    """用当前仓库快照注入常见漂移，验证检查器不是人工约定。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = load_documents()
        cls.texts = load_document_texts()

    def _check(self):
        return check_documents(copy.deepcopy(self.documents), dict(self.texts))

    @staticmethod
    def _clone_root(name: str) -> Path:
        """创建不会回读真实仓库的临时 Checker clone；测试结束后由系统临时目录回收。"""

        target = Path(tempfile.mkdtemp(prefix=f"protocol_consistency_{name}_")) / "repo"
        shutil.copytree(
            ROOT,
            target,
            ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
        )
        return target

    def test_current_protocol_is_consistent(self) -> None:
        report = self._check()
        self.assertTrue(report.passed, "\n".join(report.errors))

    def test_workflow_version_drift_fails(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["workflow"]["version"] = 6
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("PROTOCOL_VERSION_DRIFT" in item for item in report.errors))

    def test_missing_runtime_state_route_fails(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["workflow"]["states"].pop("EVALUATING")
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("STATE_TRANSITION_INVALID" in item for item in report.errors))

    def test_module_cannot_become_fourth_agent(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["role_policy"]["roles"]["domain_research"] = {}
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("CORE_ROLE_DRIFT" in item for item in report.errors))

    def test_product_and_plan_approval_gates_cannot_merge(self) -> None:
        documents = copy.deepcopy(self.documents)
        documents["workflow"]["state_guards"]["WAITING_FOR_PLAN_REVIEW"].pop(
            "approved_plan_must_be_null"
        )
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("PRODUCT_PLAN_GATE_MERGED" in item for item in report.errors))

    def test_global_claim_without_qualification_fails(self) -> None:
        documents = copy.deepcopy(self.documents)
        rollout = documents["f14"]["f14"]["rollout"]
        rollout["mode"] = "global"
        rollout["global_enabled"] = True
        documents["f14"]["f14"]["selective_context"]["enabled"] = True
        documents["f14"]["f14"]["evaluator_selective_context"]["enabled"] = True
        documents["f14"]["f14"]["invocation_gate"]["enabled"] = True
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("F14_GLOBAL_BYPASS" in item for item in report.errors))

    def test_selective_feature_requires_controlled_evidence(self) -> None:
        documents = copy.deepcopy(self.documents)
        section = documents["f14"]["f14"]
        section["selective_context"]["enabled"] = True
        section["qualification_evidence"]["controlled"] = "BLOCKED"
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("F14_FEATURE_GATE_BYPASS" in item for item in report.errors))

    def test_invocation_gate_requires_fault_injection_and_semantic_zero(self) -> None:
        documents = copy.deepcopy(self.documents)
        section = documents["f14"]["f14"]
        section["invocation_gate"]["enabled"] = True
        section["qualification_evidence"]["fault_injection"] = "BLOCKED"
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("F14_FEATURE_GATE_BYPASS" in item for item in report.errors))

    def test_evaluator_selective_feature_requires_real_parity_evidence(self) -> None:
        documents = copy.deepcopy(self.documents)
        section = documents["f14"]["f14"]
        section["evaluator_selective_context"]["enabled"] = True
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("F14_FEATURE_GATE_BYPASS" in item for item in report.errors))

    def test_checker_reads_schema_and_template_from_clone_root(self) -> None:
        clone = self._clone_root("schema")
        schema_path = clone / "config" / "schemas" / "project_v7.schema.json"
        schema_path.write_text(
            schema_path.read_text(encoding="utf-8").replace(
                '"project_v6.schema.json"', '"broken.schema.json"', 1
            ),
            encoding="utf-8",
        )
        template_path = clone / "templates" / "project.yaml"
        template_path.write_text(
            template_path.read_text(encoding="utf-8").replace(
                "schema_version: 7", "schema_version: 6", 1
            ),
            encoding="utf-8",
        )
        report = run_check(clone)
        self.assertTrue(any("SCHEMA_VERSION_DRIFT" in item for item in report.errors))

    def test_checker_reads_role_selector_from_clone_root(self) -> None:
        clone = self._clone_root("role_selector")
        role_selector_path = clone / "runtime" / "role_selector.py"
        role_selector_path.write_text(
            role_selector_path.read_text(encoding="utf-8").replace(
                'Selection("WAIT"', 'Selection("BROKEN"', 1
            ),
            encoding="utf-8",
        )
        report = run_check(clone)
        self.assertTrue(any("WAIT_ROLE_BYPASS" in item for item in report.errors))

    def test_checker_reads_retry_governance_from_clone_root(self) -> None:
        clone = self._clone_root("retry")
        retry_path = clone / "config" / "retry_governance.yaml"
        retry_path.write_text(
            retry_path.read_text(encoding="utf-8").replace(
                "maximum_automatic_iterations: 5",
                "maximum_automatic_iterations: 99",
                1,
            ),
            encoding="utf-8",
        )
        report = run_check(clone)
        self.assertTrue(any("EVALUATOR_RETRY_DRIFT" in item for item in report.errors))

    def test_manifest_route_contract_is_a_research_gate_not_linear_sequence(self) -> None:
        documents = copy.deepcopy(self.documents)
        route = documents["protocol_manifest.yaml"]["route_contract"]["requirements_discovery"]
        route["research_execution_decisions"] = ["required"]
        report = check_documents(documents, dict(self.texts))
        self.assertTrue(any("RESEARCH_CONTRACT_INVALID" in item for item in report.errors))


if __name__ == "__main__":
    unittest.main()
