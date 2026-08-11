# Session Event 协议

## 不可变性

Event 只允许追加；SQLite trigger 拒绝 UPDATE 和 DELETE。每个 Session 内
`sequence` 连续递增，`idempotency_key` 唯一。Event ID 由 Session 和 sequence
确定性生成。

## 字段

Event 保存 Session、sequence、带时区时间、actor、类型、因果 Event、关联 ID、
幂等键、canonical JSON Payload 及 SHA-256。Payload 最大 64 KiB，疑似凭据字段
被拒绝；长工具输出只保存引用。

## 状态提交事件

```text
PROJECT_STATE_COMMIT_REQUESTED
    -> 原子写 project.yaml
    -> PROJECT_STATE_COMMITTED
```

revision 或 hash 不一致时追加 `PROJECT_STATE_CONFLICT`，不得静默覆盖。
项目文件提交后、成功 Event 前崩溃时，Recovery 依据 revision/hash 补齐唯一记录。

## E1 Model Invocation 审计

`model_invocations` 与 Durable Session 分开记录。每条 Invocation 绑定
`session_id`、`invocation_id`、`role`、`source_revision`、`context_manifest_id`、
`model_id`、`capability_profile`、`fresh_context_required` 和
`context_isolation`。Evaluator 必须使用新的 Invocation；Runtime 不允许在同一
Session 中存在仍为 ACTIVE 的上一 Invocation 后启动新的 Evaluator Invocation。

Event 只保存身份、状态、revision 和 hash，不保存模型输出正文、Generator 推理或
凭据。恢复结束中断 Invocation 后，重试必须创建新的 Invocation，不能把旧 Invocation
的自由文本历史恢复为 Evaluator Context。

## Role Execution 审计事件

每次正式 Role Run 追加以下事件（事件名保持 Runtime `EventType` 风格）：

```text
ROLE_EXECUTION_REQUESTED
ROLE_THREAD_CREATED              # 仅真实 Child Thread
ROLE_EXECUTION_STARTED
ROLE_EXECUTION_FALLBACK          # 记录实际降级原因
ROLE_EXECUTION_COMPLETED
ROLE_EXECUTION_FAILED
ROLE_EXECUTION_CANCELLED
ROLE_THREAD_ARCHIVED             # 宿主线程完成终止/归档时
```

事件只保存 `role_execution_id`、`run_id`、Role、实际模式、稳定 thread ID（若真实
存在）、source revision 和安全原因。`FRESH_INVOCATION` 不产生伪造的 thread ID。
Role Execution、Model Invocation、Context Manifest 与 Phase Attestation 的绑定由
Runtime 校验，不能依靠模型输出自报。
