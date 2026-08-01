# Runtime 恢复协议

## 原则

恢复只依据持久化事实，不依赖原进程内存；重复执行必须幂等。恢复前必须取得或
接管过期 Lease，旧 Worker 的 lease version 不能提交或释放新 Lease。

## revision 窗口

- 请求已记录、YAML 未提交：若 revision/hash 仍是 before，标记 pending 为
  ABORTED，可由上层重新执行。
- YAML 已提交、成功 Event 未记录：若 revision/hash 等于 after，标记
  COMMITTED 并补写唯一恢复 Event。
- 两侧都不匹配：停止并抛出 RecoveryError，不猜测或覆盖。

## 其他窗口

- 已完成 Tool Call：从 `tool_calls.result_reference` 恢复，不重复调用工具。
- Evaluation `RECOVERY_REQUIRED`：校验 Issue/报告和暂存候选状态，再通过
  Runtime CAS 重放状态提交。
- Session DB 存在但项目缺失：抛出 ProjectMissingError。
- 项目存在但 DB 缺失：报告缺失，不静默创建历史。
- Lease 过期：新 Worker 事务性接管并递增 `lease_version`。
