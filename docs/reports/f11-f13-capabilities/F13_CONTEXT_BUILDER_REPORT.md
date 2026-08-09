# F13.1 Deterministic Context Builder

正式实现位于 `runtime/context/`，配置位于 `config/context.yaml`。旧的
`experimental/f13_context_builder/` 仅作为历史原型保留，不是正式调用路径。

- 按 Planner、Generator、Evaluator 与 workflow state 选择 Context 来源。
- 每个来源提供 reference、content hash 和 reason；Package 计算稳定 context hash。
- 项目根目录来自 F10 Session；读取通过现有 Capability 与 Path Policy。
- Secret、Control Plane raw files 和跨项目路径被拒绝。
- 构建只通过 F10 `SessionStore` 追加轻量 `CONTEXT_BUILT` Event。

F13.1 不实现 Token Budget、Resume Context、搜索、模型调用或并行 Agent。
