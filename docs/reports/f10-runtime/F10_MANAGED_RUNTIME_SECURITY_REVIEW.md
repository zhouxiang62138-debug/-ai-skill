# F10 安全复核

## 已落实

- Event、Session、Lease、Checkpoint、Tool Call、revision 均由 SQLite 约束和 Python
  模型验证。
- Event sequence、ID、幂等键、状态 revision 与角色选择不由 Prompt 决定。
- Event 使用 canonical JSON、SHA-256、64 KiB 上限，并拒绝疑似 Secret 字段。
- Event 表有 SQLite trigger，禁止 UPDATE/DELETE。
- v7 `project.yaml` 直接 writer 被拒绝；CAS 每次提交前验证 Lease、revision 和 hash。
- Lease 使用 worker ID + version fencing，过期接管在 SQLite 事务内完成。
- Evaluation 恢复仅在报告/Issue/暂存状态完整时重放，缺失时拒绝伪造。

## 自审结果

未发现可使旧 Worker 覆盖新 Worker、重放 Event 改写历史、或通过遗留 writer 绕过 v7
CAS 的路径。全量 366 项测试通过，包含超大 Payload、疑似凭据字段、状态冲突、崩溃前后
提交和恢复幂等测试。

## 有意留待后续阶段

- F11：统一执行环境、Generator Shell 隔离、cwd/符号链接执行边界、Docker。
- F12：Vault、短期凭据、外部服务代理、默认断网、Prompt Injection 与环境变量扫描。
- F13：Context Builder、上下文压缩、多 Worker/任务图。

这些事项未被误标为已完成，F10 不自动进入后续阶段。
