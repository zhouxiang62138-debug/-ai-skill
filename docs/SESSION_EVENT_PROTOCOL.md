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
