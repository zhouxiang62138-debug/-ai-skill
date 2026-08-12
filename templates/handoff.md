# 交接记录 <编号>

## 来源角色与目标角色

## 输入工件

## 已完成工作

## 验证证据与限制

- 当前返工记录（如有）：
- 当前结构化 Issue Package（如有）：
- Generator 逐项回应：`memory/handoffs/responses/evaluation-<nnn>-response.yaml`
- 本轮 `evidence_v2` 工件：
- `current_iteration` 是否由 Evaluator 唯一递增：

## 结构化仓库引用

```yaml
changed_project_artifacts: []
changed_target_files: []
verification_artifacts: []
```

每项使用：

```yaml
repository: working_repository
path: scripts/example.py
```

## Approved Reference Bindings（如适用）
- Implemented Reference Bindings: 逐条列出 `REFDEC-*`、对应 Task 和 AC，以及实际状态。
- Not Implemented / Excluded: 明确列出未实施、被排除或被阻塞的绑定。
- Deviations: 记录与批准绑定的事实偏差、原因和验证证据。
- Handoff 不得宣称 Reference Conformance PASS、Visual Similarity PASS 或 Evaluation PASS。

## Evaluator Reference Conformance 输入
- `reference_conformance` 由 Evaluator 在 Evidence Manifest 阶段生成；Generator Handoff 只能作为实现事实来源。
- 每个 `REFDEC-*` 必须回指批准的 Task/AC，并引用 `REF-EV-*` 独立证据。
- `REF-EV-*` 必须标明 `evaluator_owned: true`、证据类型和底层 Evidence ID；不能直接引用原始 Reference Archive。
- 没有批准 Contract 时记录 `NOT_APPLICABLE`；视觉能力不可用时记录 `BLOCKED`。

## 下一步
