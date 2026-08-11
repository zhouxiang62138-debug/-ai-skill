# Role Thread Isolation：实现报告

## 已实现

- `runtime/role_execution.py`：Host Capability、Role Execution Request、Workspace
  Binding、RoleExecutionBroker 和默认不可用 Host。
- `config/role_execution.yaml`：Child Thread 优先、Fresh Invocation fallback、
  每个 Role Run fresh、等待态不创建线程、Runtime CAS 权威策略。
- `runtime/session_store.py`：Role Execution 表、schema v4 迁移、绑定/完成/失败/
  崩溃未知状态、Role Run 源状态元数据和 Attestation 扩展字段。
- `runtime/phase_runner.py`：Context → Role Execution → Invocation → Host 调用 →
  Verifier/Attestation → CAS → 归档的正式链路。
- `runtime/orchestrator.py`：Host Adapter 注入、inspect 元数据、暂停取消、提交前
  Role Execution Attestation 绑定检查。
- `runtime/recovery.py` 与 `runtime/event_types.py`：生命周期事件和崩溃恢复。

## 兼容策略

既有只创建 Role Run/Invocation 的测试与历史记录仍可读取；新正式 PhaseRunner
自动创建 Role Execution。新生产提交若存在活动 Role Execution，则缺失匹配
Attestation 会被拒绝。

## 没有实现的内容

仓库未实现 Codex App Server 或厂商 SDK 适配器，也未声称当前宿主一定支持真实
Child Thread。真实 Host 需要实现 `RoleExecutionHost` 并由宿主注入。
