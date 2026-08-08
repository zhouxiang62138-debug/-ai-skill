# Managed Runtime 架构

## 分层

```text
SQLite Session Store
        ↓
Deterministic Orchestrator
        ↓
Stateless Role Harness 请求/结果信封
        ↓
Runtime Infrastructure
  ├─ F11 ExecutionBroker / ExecutionEnvironment / Snapshot
  ├─ F12 Capability / Credential / Network Policy
  └─ F13 Deterministic Context Builder / Resume
        ↓
Planner / Generator / Evaluator
        ↓
现有文件驱动业务工作流
```

F10 提供前三层的耐久控制骨架；F11、F12、F13 已在正式 Runtime 路径中增量接入，
分别负责执行、托管安全和确定性上下文。Docker Sandbox 仍为 `DEFERRED`，
LocalCompatibilityEnvironment 不是物理 Sandbox；本文件不把它们描述为同一能力。

当前正式、实验和延期能力的唯一状态矩阵见
`docs/RUNTIME_CAPABILITY_STATUS.md`。多 Worker 并行和第四个 Agent 仍不属于本架构。

## Session Control Plane

正式 Session Store 位于项目目录之外：
`~/.ai-development-team/runtime/<control_plane_id>/sessions.sqlite3`，同级保存
`tool-results/`、`checkpoints/` 与 `locks/`。`project.yaml.runtime` 仅保存
`session_id`、`control_plane_id` 与业务 revision；缺失外部 Session Store 时必须
以 `RUNTIME_HISTORY_MISSING` 阻断，不得静默重建历史。

## 权威来源

- `project.yaml`：业务当前状态的权威投影。
- SQLite：Session 运行历史、Event、Lease、Checkpoint、Tool Call 和 revision。
- `memory/`、`evaluation/`、`change_requests/`、`releases/`：追加式业务事实。

三个来源通过 ID、revision、hash 和引用关联，任何摘要都不能覆盖原始事实。

## 安全边界

Orchestrator 不是 Agent，只执行定位、校验、选角、Lease、事件、CAS、Checkpoint
和恢复。角色类型保持三个。v7 状态写入门禁位于 Python 代码，不依赖 Prompt。
