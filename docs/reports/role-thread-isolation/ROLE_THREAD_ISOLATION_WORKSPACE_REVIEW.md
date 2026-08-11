# Role Thread Isolation：Workspace Review

## 结论

默认使用受控共享 Workspace，权威状态仍是项目的 `project.yaml` 加外部 Session
Control Plane。Child Thread 不能直接提交权威状态；Role Result 必须回到 Runtime
Verifier、Lease 和 CAS。

Host 声明 `WORKTREE` 时，如果没有可验证的同步/rebase/checkout 流程，Broker 将
Child Thread 请求降级到 `FRESH_INVOCATION`，并记录
`HOST_WORKTREE_STATE_DIVERGENCE_UNSAFE`。因此不会让 Planner、Generator、Evaluator
各自维护冲突的 `project.yaml`。

当前测试覆盖了 worktree capability downgrade；既有 ProjectStateCAS 回归继续负责
旧 revision 的 `CAS_CONFLICT` / `STALE_REVISION` 保护。
