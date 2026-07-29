# Planner → First-Ask Intake 交接 <编号>

> 本文件只请求补充基础事实，不替用户填写答案。创建后禁止覆盖。

## 交接元数据

- 项目 ID：
- 交接编号：
- Planner 使用的需求版本：
- Planner 使用的需求快照：
- 创建时间：

## 触发原因

- 类型：`missing_fact` / `changed_fact` / `conflicting_fact`
- 发现位置：
- 对产品规划的影响：

## 需要补充的事实

| 建议 `question_key` | 对应需求字段 | 当前状态 | 为什么必须补充 |
| --- | --- | --- | --- |
|  |  |  |  |

## 已有来源

<!-- 列出相关需求快照、采访或用户反馈，禁止改写原文件。 -->

- 

## Planner 未采用默认值的原因

<!-- 说明为什么没有安全、可逆的低风险默认值。 -->

## 路由要求

```yaml
status: INTAKE
next_role: null
active_module: first_ask_intake
latest_handoff: memory/handoffs/intake-request-<nnn>.md
```

## 边界

- First-Ask 应创建新的采访和完整需求快照版本。
- First-Ask 不得修改产品方案或规划状态字段。
- Planner 不得修改现有需求记录。

