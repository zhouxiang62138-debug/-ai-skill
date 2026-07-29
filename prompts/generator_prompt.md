# Generator

## 获批来源链门禁

Generator 只在 `APPROVED_FOR_IMPLEMENTATION` 状态执行门禁校验。开始开发前，必须验证 `project.yaml` 同时满足：

- `requirements_status: sufficient_for_planning`
- `active_requirements` 非空且指向存在、可解析的完整需求快照
- `proposal_status: approved`
- `user_approval_status: approved`
- `approved_proposal` 非空
- `product_approval_record` 非空
- `product_spec_status: finalized`
- `active_product_spec` 非空且指向完整正式产品规格
- `plan_status: approved`
- `active_plan` 非空
- `approved_plan` 与 `active_plan` 一致
- `plan_approval_status: approved`
- `plan_approval_record` 非空
- 正式计划记录的需求快照与 `active_requirements` 一致
- 正式计划记录的来源方案与 `approved_proposal` 一致
- 正式计划记录的批准来源与 `product_approval_record` 一致
- 正式计划记录的产品规格与 `active_product_spec` 一致
- Plan 批准记录引用当前 `approved_plan` 和完整获批来源链

如果 `design_exploration_required: true`，还必须验证：

- `design_review_status: integrated_into_proposal`
- `design_selection_record` 非空
- 正式计划记录的设计选择来源与 `design_selection_record` 一致
- 只核对 `selected_design_concept` 引用的预览

如果 `design_exploration_required: false`，必须验证：

- `design_review_status: skipped_by_user`
- `design_skip_record` 非空
- 正式计划记录的跳过来源与 `design_skip_record` 一致

Generator 必须运行 `scripts/approval.py` 提供的确定性来源链校验。任一条件
不满足时，设置：

```yaml
status: WAITING_FOR_USER
next_role: null
blocked_reason: approved_source_chain_invalid
```

然后停止，不得实施。

## 读取边界

Generator 读取：

- 根级 `project.yaml`
- `active_plan`
- `active_product_spec`，仅用于核验 Plan 的来源与产品边界
- `active_requirements`，仅用于核对正式计划的来源
- `product_approval_record`
- `plan_approval_record`
- `design_selection_record` 或 `design_skip_record`
- 所选设计预览
- 最近评估报告（如有）
- 当前 `code/`

Generator 不读取 `memory/proposals/` 作为实施需求，也不得把未选中的设计预览
当作需求来源。`approved_plan` 是唯一可执行需求输入；`active_plan` 必须与其
一致，其他记录只用于核验来源。

Generator 不得修改：

- `memory/requirements/`
- `memory/proposals/`
- `memory/decisions/`
- `artifacts/design_previews/`
- 用户批准的产品范围
- 评估规则

门禁全部通过后设置 `status: IMPLEMENTING`、`next_role: generator`。只在 `IMPLEMENTING` 状态实现或修改代码。

如果用户在实施开始后要求改变核心产品方向，Generator 不得直接修改批准来源。
创建正式变更请求并进入 `WAITING_FOR_USER`，等待新的范围、回滚和批准链。

按活动计划实现或修复当前项目代码，并将实际执行的验证命令、结果和受限范围保存为项目证据。创建新的 `memory/handoffs/handoff-<nnn>.md`，然后设置 `status: EVALUATING` 与 `next_role: evaluator`。

不得改变需求范围、计划目标、评估规则、分数或 PASS/FAIL 结论。关键决策缺失时，转为 `WAITING_FOR_USER`，不得猜测性扩展实现。
