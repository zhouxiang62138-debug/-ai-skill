# Managed Runtime 架构

## 分层

```text
SQLite Session Store
        ↓
Deterministic Orchestrator
        ↓
Stateless Role Harness 请求/结果信封
        ↓
Planner / Generator / Evaluator
        ↓
现有文件驱动业务工作流
```

F10 只实现前三层的耐久控制骨架，不实现 Docker、凭据代理、通用执行环境、
Context Builder 或多 Worker 并行。

## 权威来源

- `project.yaml`：业务当前状态的权威投影。
- SQLite：Session 运行历史、Event、Lease、Checkpoint、Tool Call 和 revision。
- `memory/`、`evaluation/`、`change_requests/`、`releases/`：追加式业务事实。

三个来源通过 ID、revision、hash 和引用关联，任何摘要都不能覆盖原始事实。

## 安全边界

Orchestrator 不是 Agent，只执行定位、校验、选角、Lease、事件、CAS、Checkpoint
和恢复。角色类型保持三个。v7 状态写入门禁位于 Python 代码，不依赖 Prompt。
