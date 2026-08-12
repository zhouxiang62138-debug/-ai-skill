"""让 F14-C focused pytest 测试进入仓库的 unittest 全量发现。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests import test_f14_c1_semantic_model as c1
from tests import test_f14_c4_enforced_gate as c4
from tests import test_f14_c5_selective_candidate as c5
from tests import test_f14_c6_fault_injection as c6
from tests import test_f14_c_preflight as c0
from tests import test_f14_c_shadow_coverage as shadow
from tests import test_f14_c_shadow_first_runtime as runtime
from tests import test_f14_c_storage as storage


def _with_tmp_path(test_function) -> None:
    """为原 pytest 测试函数提供等价的临时目录参数。"""

    with tempfile.TemporaryDirectory(prefix="test_f14_c_unittest_bridge_") as directory:
        test_function(Path(directory))


class F14CDiscoveredTests(unittest.TestCase):
    """一对一执行 21 个原 focused test，保持 focused 与 full suite 同源。"""

    def test_c0_control_plane_path_is_outside_project_workspace(self) -> None:
        _with_tmp_path(c0.test_c0_control_plane_path_is_outside_project_workspace)

    def test_c0_authority_cannot_be_spoofed_by_locator_or_revision(self) -> None:
        _with_tmp_path(c0.test_c0_authority_cannot_be_spoofed_by_locator_or_revision)

    def test_c0_real_model_boundary_counts_adapter_failure_as_llm(self) -> None:
        _with_tmp_path(c0.test_c0_real_model_boundary_counts_adapter_failure_as_llm)

    def test_c1_semantic_model_is_role_task_revision_scoped_without_delivery_change(self) -> None:
        _with_tmp_path(c1.test_c1_semantic_model_is_role_task_revision_scoped_without_delivery_change)

    def test_global_constraint_without_direct_graph_edge_is_still_mandatory(self) -> None:
        shadow.test_global_constraint_without_direct_graph_edge_is_still_mandatory()

    def test_shadow_candidate_can_show_reduction_only_when_mandatory_is_complete(self) -> None:
        shadow.test_shadow_candidate_can_show_reduction_only_when_mandatory_is_complete()

    def test_stale_and_hash_conflict_never_pass_coverage(self) -> None:
        shadow.test_stale_and_hash_conflict_never_pass_coverage()

    def test_c4_incomplete_mandatory_coverage_blocks_before_model_call(self) -> None:
        c4.test_c4_incomplete_mandatory_coverage_blocks_before_model_call()

    def test_c4_pass_keeps_f13_full_context_and_does_not_deliver_candidate(self) -> None:
        c4.test_c4_pass_keeps_f13_full_context_and_does_not_deliver_candidate()

    def test_c4_optimizer_crash_falls_back_to_existing_f13_context(self) -> None:
        c4.test_c4_optimizer_crash_falls_back_to_existing_f13_context()

    def test_c5_eligible_candidate_is_only_an_evaluation_artifact(self) -> None:
        c5.test_c5_eligible_candidate_is_only_an_evaluation_artifact()

    def test_c5_unknown_authority_forces_safe_fallback(self) -> None:
        c5.test_c5_unknown_authority_forces_safe_fallback()

    def test_c1_c5_derived_records_are_append_only_and_integrity_checked(self) -> None:
        storage.test_c1_c5_derived_records_are_append_only_and_integrity_checked()

    def test_shadow_first_runtime_persists_evidence_without_replacing_f13_package(self) -> None:
        _with_tmp_path(runtime.test_shadow_first_runtime_persists_evidence_without_replacing_f13_package)

    def test_case_missing_acceptance_criterion_is_not_pass(self) -> None:
        c6.test_case_missing_acceptance_criterion_is_not_pass()

    def test_case_unknown_edge_connected_to_mandatory_is_unknown_impact(self) -> None:
        c6.test_case_unknown_edge_connected_to_mandatory_is_unknown_impact()

    def test_case_requirement_plan_conflict_is_not_pass(self) -> None:
        c6.test_case_requirement_plan_conflict_is_not_pass()

    def test_case_stale_approved_scope_is_fail_closed(self) -> None:
        c6.test_case_stale_approved_scope_is_fail_closed()

    def test_case_optimizer_unavailable_falls_back_without_empty_context(self) -> None:
        c6.test_case_optimizer_unavailable_falls_back_without_empty_context()

    def test_case_role_scope_is_not_cross_role_coverage(self) -> None:
        c6.test_case_role_scope_is_not_cross_role_coverage()

    def test_case_candidate_missing_security_constraint_never_passes(self) -> None:
        c6.test_case_candidate_missing_security_constraint_never_passes()

