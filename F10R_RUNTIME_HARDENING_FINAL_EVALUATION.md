# F10R 最终评估（未完成）

## 2026-08-01 最终验收结论

本节为当前分支的最终结论。验证范围严格限定为 F10；F11、F12、F13 继续仅作为
`experimental/` 中的原型，未被正式 Runtime、工作流或 `SKILL.md` 启用。

| 验收项 | 结论 | 证据 |
|---|---|---|
| Session 独立性与缺失阻断 | PASS | `runtime/control_plane.py`；`tests/test_control_plane.py`、`tests/test_rebind.py` |
| Lease 独占、Token 与 Fencing | PASS | `runtime/leases.py`；`tests/test_worker_lease.py`、`tests/test_project_revision_cas.py` |
| CAS 幂等、字段权限与状态转换 | PASS | `runtime/project_revision.py`；`tests/test_project_revision_cas.py`、`tests/test_runtime_policy.py` |
| Tool 状态机、不可变结果与原子事件 | PASS | `runtime/session_store.py`；`tests/test_tool_call_recovery.py`、`tests/test_crash_after_tool_completion.py` |
| Recovery | PASS | `runtime/project_revision.py`、`runtime/session_store.py`；`tests/test_crash_before_project_state_commit.py`、`tests/test_crash_after_project_state_commit.py` |
| Migration 与 Rollback | PASS | `scripts/project_migration.py`；`tests/test_migration_rollback.py` |
| CLI Step 协议与 Lease 生命周期 | PASS | `runtime/cli.py`、`runtime/orchestrator.py`；`tests/test_orchestrator_role_selection.py` |
| 配置与文档一致性 | PASS | `config/runtime.yaml`、`config/workflow.yaml`；`tests/test_runtime_documentation.py`、`tests/test_runtime_policy.py` |
| 原有业务回归 | PASS | `python -m pytest -q`：409 passed、104 subtests passed |

**F10R 总结：PASS。** 未同步安装副本、未合并主分支、未启动 F11–F13。

| 项目 | 结论 |
|---|---|
| Session 独立性 | PARTIAL |
| Lease 独占与 Fencing | PARTIAL |
| CAS 幂等与字段权限 | PARTIAL |
| Tool Result 完整性 | PARTIAL |
| Recovery | FAIL |
| Migration/Rollback | FAIL |
| CLI 生命周期 | FAIL |
| 文档一致性 | FAIL |
| 原有业务回归 | PASS（381 passed，104 subtests passed） |

**F10R 总结：FAIL。** 本文件不得作为正式同步依据。

## 2026-08-01 追加证据更新

本节不覆盖上述历史结论。当前分支 `codex/f10-runtime-correctness-hardening` 已完成外部
Control Plane、随机 Worker/Token fencing、声明式 Patch CAS、工作流状态迁移校验、工具
Attempt、显式 rebind、迁移阶段记录与 Step CLI 的部分加固。完整回归命令
`python -m pytest -q` 已通过 **392 passed, 104 subtests passed**。

| 验收项 | 当前结论 | 证据/限制 |
|---|---|---|
| Session 独立性 | PASS | `runtime/control_plane.py` 与 `tests/test_control_plane.py` |
| Lease/Fencing | PASS | `runtime/leases.py`、`tests/test_worker_lease.py` |
| CAS 幂等、字段归属、状态迁移 | PASS | `runtime/project_revision.py`、`tests/test_project_revision_cas.py`、`tests/test_runtime_policy.py` |
| Tool 状态与结果完整性 | PARTIAL | 原子事件、不可变结果与 Attempt 已覆盖；仍缺完整执行器的超时/副作用确认流程 |
| Recovery | PARTIAL | revision/工具结果基本对齐已覆盖；Evaluation 实际异常全矩阵仍未完全证明 |
| Migration/Rollback | PARTIAL | 生命周期记录与 detached Session 已覆盖；中断故障注入和 retry 覆盖不足 |
| CLI 生命周期 | PARTIAL | Step 命令与 Lease 生命周期可用；缺真实子进程退出端到端证据 |
| 文档一致性 | PARTIAL | F11–F13 范围与核心协议已更新；所有历史报告逐行复核尚未完成 |
| 既有业务回归 | PASS | 当前完整 pytest 回归 |

**当前总体结论：FAIL（尚不得同步安装副本或宣布正式发布）。**
