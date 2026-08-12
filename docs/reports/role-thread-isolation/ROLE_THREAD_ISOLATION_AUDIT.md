# Role Thread Isolation：T0 审计

## 审计结论

- 当前核心 Agent：Planner、Generator、Evaluator，未新增第四个业务 Agent。
- `runtime/orchestrator.py` 是确定性 Host/Control Surface；`runtime/phase_runner.py`
  通过 `ModelInvocationAdapter.invoke()` 调用模型抽象。
- `ModelInvocationAdapter` 是宿主无关抽象，不是 Codex Child Thread API；仓库没有
  可程序化调用的 Codex App Server、thread spawn、fork 或稳定 Host Thread ID 接口。
- 原有 Runtime 已有 Role Run、Model Invocation、Context Builder、Lease/CAS、
  Attestation 和 F10 外部 Control Plane，但没有 Role Run ↔ Host Thread 的持久化对象。

## 能力矩阵

```text
Host Child Thread Capability: PARTIAL / HOST_UNAVAILABLE
Current Role Isolation:       FRESH_CONTEXT_INVOCATION（升级后有真实 Host Adapter 路径）
Worktree Behavior:             默认 SHARED；强制 WORKTREE 且无法同步时安全降级
Runtime Binding:               外部 SQLite Control Plane + project.yaml revision/CAS
```

本仓库不猜测当前 Codex 宿主是否支持真实 Child Thread。只有注入
`RoleExecutionHost`、能力快照显示 `supported + stable_thread_id + programmatic_spawn`
且实际返回稳定 ID 时，才会记录 `CHILD_THREAD`；默认明确记录
`HOST_CHILD_THREAD_UNAVAILABLE` 并使用 `FRESH_INVOCATION`。

## Worktree 审计

业务状态仍只允许 Runtime CAS 写入权威 `project.yaml`。默认 Workspace Binding 为
`SHARED`；Host 若声明强制 `WORKTREE` 而未提供同步保证，Runtime 不启动 Child
Thread，改用共享空间的 Fresh Invocation，避免产生第二份权威状态。
