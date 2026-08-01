# F10R Migration/Rollback 报告（进行中）

已实施：v7 迁移创建外部 Control Plane；Rollback 将对应 Session 标记为 `DETACHED`，Lease 拒绝非 `ACTIVE` Session。

未完成：中断状态机、显式 project rebind inspect/preview/apply/verify/rollback。结论：**FAIL（进行中）**。
