# 产品方案批准记录 <编号>

> 只有用户对当前产品方案作出无歧义批准时才能创建。创建后禁止覆盖。

## 批准元数据

- 项目 ID：
- 批准记录编号：
- 批准时间：

## 获批来源

- 需求版本：
- 活动需求快照：
- 获批产品方案：
- 设计选择记录：
- 设计探索跳过记录：

## 用户明确批准

<!-- 逐字记录用户批准原文，不总结或扩大授权范围。 -->

- 用户批准原文：
- 来源：

## 批准范围

- 获批 MVP：
- 明确排除项：
- 保留假设：
- 尚未决定但不阻塞开发的事项：

## 正式计划

- 生成的正式产品规格：
- 生成的待审核开发 Plan：
- 两者是否完整引用上述来源：`yes` / `no`

## 状态更新

```yaml
proposal_status: approved
user_approval_status: approved
approved_proposal: <获批产品方案路径>
product_approval_record: memory/decisions/product-approval-<nnn>.md
product_spec_status: finalized
active_product_spec: memory/specifications/product_spec_v<nnn>.md
plan_status: waiting_user_review
active_plan: memory/plans/plan-<nnn>.md
approved_plan: null
plan_approval_status: waiting_explicit_confirmation
plan_approval_record: null
status: WAITING_FOR_PLAN_REVIEW
next_role: planner
```

本记录只批准产品方案，不批准开发 Plan，也不允许 Generator 开始实施。
