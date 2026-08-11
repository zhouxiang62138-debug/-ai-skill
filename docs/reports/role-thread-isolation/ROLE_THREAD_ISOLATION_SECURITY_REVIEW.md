# Role Thread Isolation：Security Review

- Thread ID 不是权限边界；Capability Policy、Path Policy、Role Policy、Lease、
  Attestation 和 CAS 仍然有效。
- Planner、Generator、Evaluator 的读写边界没有因 Child Thread 放宽。
- `host_thread_id` 只在 Host 返回真实稳定 ID 后写入；Fresh Invocation 禁止写入。
- Role Execution 与 Invocation 必须属于同一 Session、Role Run、Role、Context 和
  source revision，跨 Run 绑定会拒绝。
- Workspace Binding 只允许 `RUNTIME_CAS` / `RUNTIME_CAS_ONLY` 权威组合。
- Event Payload 继续走现有大小上限、canonical JSON 和疑似 Secret 拒绝。
- Main Thread 没有新增业务提交捷径；没有 Attestation 的 commit 仍被拒绝。

安全结论：Role Execution 是隔离与可审计性组件，不是绕过 Role Policy 的信任边界。
