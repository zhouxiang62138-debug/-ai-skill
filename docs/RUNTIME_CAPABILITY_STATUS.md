# Runtime 能力状态矩阵

> 本文件是 Skill 本体关于 Runtime 能力状态的当前权威说明，更新时间：2026-08-09。
> 历史评审报告保留当时的结论，但不覆盖本文件的当前状态。

## Formal Production Runtime

以下能力已进入正式 Runtime 路径，并由 `runtime/`、`config/`、`scripts/` 与对应
回归测试共同维护：

- **F10 Durable Runtime**：SQLite Session Store、Event History、Worker Lease /
  Fencing、Revision / CAS、Checkpoint、Crash Recovery、Pause / Resume，以及确定性
  Orchestrator。
- **F11 Execution Runtime**：`runtime/execution/` 中的 ExecutionBroker、统一执行
  Contract、LocalCompatibilityEnvironment、路径策略、Tool Call 生命周期和
  Snapshot / Restore。
- **F12 Managed Security Runtime**：Capability Policy、Credential Broker、Network /
  External Tool Policy，以及 Secret Boundary；默认拒绝仍由代码和配置共同强制。
- **F13 Deterministic Context Runtime**：`runtime/context/` 中的 Context Builder、
  Context Budget、Role Scoping、Context Manifest、Incremental Resume，以及与 F10
  Session 解耦的 Rollover/Fresh Invocation。
- **Best Validated Candidate Runtime**：`runtime/candidates.py` 只通过既有 Snapshot Service
  执行已验证 Candidate 的恢复，Evaluator 只能写推荐记录。

正式 Runtime 仍只有 Planner、Generator、Evaluator 三个 Agent。First-Ask、Change
Request、ExecutionBroker、Context Builder 和其他协调能力都是 Module、Broker、Policy
或确定性基础设施，不是第四个 Agent。

## Experimental Prototype

`experimental/` 下的以下目录只保留为历史原型，不是正式调用路径：

- `experimental/f11_execution_environment/`
- `experimental/f12_security_boundary/`
- `experimental/f13_context_builder/`

对应的历史报告可以描述原型当时的能力，但不能用来否定 `runtime/` 下已经正式交付
的 F11–F13 能力。

## Deferred Capability

- **Docker Sandbox / 物理进程隔离**：继续为 `DEFERRED`。Docker daemon 不可用时，
  只影响 Docker 专项验证，不阻塞受控本机项目的正式 Runtime；
  `LocalCompatibilityEnvironment` 也不等于物理 Sandbox。

## 读取规则

需要判断当前状态时，优先读取本文件和
`docs/reports/production-readiness/FINAL_PRODUCTION_READINESS_REVIEW.md`；带有
“历史快照”标记的报告只用于追溯当时的基线、发现和修复过程。
