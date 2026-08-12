# F13 Context System Final Review

## Capability / Builder

- Context 由 F10 Session、项目状态、Role 和 Workflow State 确定性构建。
- Planner、Generator、Evaluator 使用独立配置来源；Role/State 不匹配、跨项目引用和 Control Plane raw file 引用会被拒绝。
- 来源包含 reference、content hash、reason；排序与 `context_hash` 稳定。
- Context Builder 是基础设施，不是 Agent，不执行代码，也不读取 Secret。

## Budget

- 三个 Role 使用 `config/context.yaml` 的独立字节/来源预算。
- `REQUIRED → HIGH → NORMAL → REFERENCE_ONLY` 顺序、硬限制、完整来源与可追踪 omitted metadata 均由代码执行。
- 超预算的 REQUIRED 明确失败；非 REQUIRED 只能完整降级为 REFERENCE，不做静默截断。

## Incremental / Resume

- 前序 Manifest 持久化在 F10 `SessionStore`，限定同一 Session、Project、Role。
- 当前来源每次重新授权并重新读取，再按 hash 产生 `UNCHANGED / ADDED / MODIFIED / REMOVED`。
- Revision、Context Policy 和 Budget 指纹参与恢复判断；损坏或无效 Manifest 不被信任；不依赖模型记忆。
- Full Build 与 Incremental Resume 的最终逻辑 `context_hash` 等价。

## Security / Boundaries / Audit

- F13 不修改 workflow state、不执行代码、不操作 Secret、不绕过 ExecutionBroker，不创建第二套 Session Store 或第四 Agent。
- F12 Capability、F11 Path Policy、单项目边界和 Secret 检查在 Resume 前重新执行。
- `CONTEXT_BUILT` / `CONTEXT_RESUMED` 复用 F10 Event；Event 和 durable Manifest 仅保存安全元数据，不保存 Context 正文或 Secret。

## Tests / Verdict

- F13 专项：34 passed。
- 全量回归：505 passed, 5 skipped, 104 subtests passed。
- 5 个 skipped 为 Docker daemon 不可用，属于 F11 Docker Sandbox deferred，不计入 F13 PASS 证据。

`F13 FINAL REVIEW: PASS`
