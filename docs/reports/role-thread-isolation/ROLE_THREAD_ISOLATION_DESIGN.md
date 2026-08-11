# Role Thread Isolation：T1–T3 设计

## 对象

`role_runs` 保留既有 `run_id`，并补充 `role_run_id` 对外别名、`project_id`、
`source_status`、`source_revision` 和 `started_at`。

`role_executions` 是新增的 Runtime 对象，字段包括：

```text
role_execution_id, session_id, run_id, role, project_id
execution_mode, requested_mode, host_thread_id, invocation_id
context_manifest_id, workspace_binding, source_revision
status, fallback_reason, started_at, completed_at, termination_reason
```

`execution_mode` 只有 `CHILD_THREAD` 与 `FRESH_INVOCATION`。不存在“模拟 Child
Thread”模式。

## Broker 边界

`RoleExecutionBroker` 提供 `capabilities()`、`start_role_execution()`、
`bind_invocation()`、`invoke()`、`complete()`、`fail()`、`cancel_active()` 和
`recover()`。Orchestrator 不直接写死任何宿主 API。

## 模式选择

配置默认请求 Child Thread，fallback 为 Fresh Invocation。能力必须同时满足真实
支持、稳定 ID、程序化创建；任一条件不满足就记录 fallback。Fresh Invocation
必须使用新的 Model Invocation，不复用完成的 Role Execution。

## 权威与安全

Context 只能来自 Deterministic Context Builder；Workspace Binding 声明
`RUNTIME_CAS` 和 `RUNTIME_CAS_ONLY`；提交前 Phase Attestation 必须绑定 Role
Execution、Invocation、Context Manifest、source revision、execution mode 和
host thread ID。
