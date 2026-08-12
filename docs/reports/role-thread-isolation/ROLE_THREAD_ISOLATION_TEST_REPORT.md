# Role Thread Isolation：Test Report

## T17 定向结果

```text
tests/test_role_thread_isolation.py: 16 passed
tests/test_role_runs.py + tests/test_phase_runner.py: 11 passed
```

覆盖项包括：

1. Planner fresh Child Thread；
2. 真实 Child Host 调用与归档；
3. Host 不可用时 Fresh Invocation fallback；
4. Role Run/Invocation 错误绑定拒绝；
5. Phase Attestation 绑定；
6. Evaluator Context 独立性；
7. Wait 无活动 execution；
8. Rework 创建新 execution；
9. Crash Recovery；
10. Worktree divergence downgrade；
11. 共享 Session Control Plane；
12. 错误 Attestation/CAS 前拒绝；
13. Pause/Lease；
14. Capability downgrade 不伪造 thread ID；
15. inspect 可观测性；
16. Child Thread 创建窗口 `REQUESTED → STARTED` 的崩溃恢复。

Evaluator Independence 原有测试未被本轮替代，需随全量回归继续运行。

## Full Regression

```text
777 passed, 6 skipped, 113 subtests passed
```

包含 Change Request、迁移、Lease/CAS、Evaluator Independence、Recovery、Browser、
Execution 和既有阶段 E2E 测试。
