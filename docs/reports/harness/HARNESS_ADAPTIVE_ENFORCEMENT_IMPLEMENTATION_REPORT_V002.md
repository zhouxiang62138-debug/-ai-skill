# Harness Adaptive Enforcement Implementation Report V002

## 对 V001 的追加变更

- `runtime/session_store.py` 新增 ACTIVE Model Invocation 查询和崩溃后幂等失败标记。
- `runtime/recovery.py` 在既有 CAS/Tool/Evaluation Recovery 前追加 Model Invocation Recovery，并通过摘要事件记录中断调用。
- `tests/test_phase_runner.py` 新增真实崩溃、Recovery、重新获取 Runtime、使用新幂等键重试闭环。

## 保持不变的边界

Recovery 不把崩溃的 Role Run 伪造为完成，不提交候选业务状态，也不重复创建旧 Invocation；恢复后由新的 Role Run 和新的幂等键继续。既有 CAS、Lease、Event append-only、Secret Boundary 与三角色约束保持有效。
