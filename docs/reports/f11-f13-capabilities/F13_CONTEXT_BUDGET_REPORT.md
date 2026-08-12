# F13.2 Context Budget + Role Scoping

- `config/context.yaml` 为 Planner、Generator、Evaluator 分别配置字节预算、来源上限与单源 Inline 上限。
- 来源按 `REQUIRED`、`HIGH`、`NORMAL`、`REFERENCE_ONLY` 稳定排序；Required 超限返回 `CONTEXT_REQUIRED_BUDGET_EXCEEDED`。
- ContextSource 明确 `INLINE` 或 `REFERENCE`，不做静默截断；非关键来源可降级或进入 `omitted_sources`。
- Package 记录预算使用量、Inline/Reference/Omitted 数量，并将选择结果纳入 `context_hash`。
- `CONTEXT_BUILT` 继续复用 F10 Event，仅记录预算元数据，不记录完整 Context。
- F12 Capability/Path Policy 仍先于预算选择；Secret 与 Control Plane raw files 不进入 Context。

验证：F13.2 预算/选择测试 10 passed；与 F13.1 组合回归 19 passed。
