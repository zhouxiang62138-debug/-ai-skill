# F9.1 结构化返工协议阶段报告

## 1. 实施摘要

阶段结果：`PASS`。

在保留 Markdown 验收报告和旧 `rework_v1` 兼容入口的同时，新增机器可读
Evaluation Issue Package、Generator 逐项回应、稳定 Issue ID、需求追踪、
确定性路由、Issue 生命周期、硬 PASS 条件和恢复型事务提交。

## 2. 架构决策

- 通用验收协议位于 `scripts/evaluation_protocol.py`，不继续堆入仅服务于
  Skill Maintenance 的 `scripts/skill_maintenance.py`。
- Issue Package 使用 `evaluation/issues/evaluation-<nnn>.yaml`。
- Generator Response 复用现有 handoff 树：
  `memory/handoffs/responses/evaluation-<nnn>-response.yaml`。
- Markdown 报告由结构化事实生成并互相引用。
- `project.yaml` 永远最后写入；状态提交失败保留 `RECOVERY_REQUIRED` 日志。

## 3. 新增文件

- `scripts/evaluation_protocol.py`
- `config/evaluation_protocol.yaml`
- `config/schemas/evaluation_issue_v1.schema.json`
- `config/schemas/generator_response_v1.schema.json`
- `templates/evaluation_issues.yaml`
- `templates/generator_issue_response.yaml`
- `tests/test_evaluation_protocol.py`
- `docs/F9_EVALUATION_PROTOCOL.md`
- `F9_1_STRUCTURED_REWORK_PROTOCOL_REPORT.md`

## 4. 修改文件

- `config/workflow.yaml`
- `config/role_policies.yaml`
- `prompts/evaluator_prompt.md`
- `prompts/generator_prompt.md`
- `templates/evaluation_report.md`
- `templates/handoff.md`
- `docs/workflow_protocol.md`
- `tests/test_exploration.py`

## 5. Schema 变化

新增 Issue Package v1 和 Generator Response v1。字段包括稳定 Evaluation/Issue ID、
11 类 Issue、5 级严重度、4 种追踪状态、6 种 Issue 生命周期状态和 6 种 Generator
回应状态。JSON Schema 提供结构约束，Python 校验器补充跨字段、文件名、汇总、
路由和来源一致性校验。

## 6. Workflow 变化

Workflow 版本由 4 升至 5，新增 `evaluation_protocol` 配置入口和固定提交顺序。
旧 `rework_governance` 标记为 `legacy_v1`，继续读取但不作为新 Evaluation 的唯一
返工输入。

## 7. 状态迁移变化

本阶段未新增状态。路由只使用现有 `IMPLEMENTING`、`PLANNING`、
`WAITING_FOR_USER`、`BLOCKED` 和 `ACCEPTED`。优先级固定为：

`SYSTEM_OR_USER > USER > PLANNER > GENERATOR > ACCEPTED`。

## 8. 测试结果

局部命令：

```text
python -m unittest -v tests.test_evaluation_protocol
```

结果：38 通过，0 失败，0 跳过。

完整回归：

```text
python -m unittest discover -s tests -v
```

结果：196 通过，0 失败，0 跳过。

## 9. 向后兼容性

- v3/v4/v5 项目状态读取行为未删除；
- 旧 Markdown Evaluation 继续保留；
- 旧 `rework_v1` 和 `evidence_v2` 测试继续通过；
- 旧项目缺少 Issue Package 时可读取历史报告，下一轮 Evaluation 才写新格式；
- Planner、Generator 和 Evaluator 原有权限边界未放宽到未声明外部目录。

## 10. 已知限制

- F9.1 的证据仍只引用路径；完整 Evidence Manifest、Gate 和受保护工件快照在
  F9.2 实现。
- 重复失败、回归趋势、提前升级和完整最大迭代治理在 F9.3 实现。

## 11. 权限边界检查

- Evaluator 未获得 `code/`、正式计划或 evaluation profile 写权限；
- Generator 未获得 `evaluation/issues/` 或 `config/evaluation_rules/` 写权限；
- 结构化路径拒绝绝对路径、UNC 和父目录穿越；
- 历史 Evaluation 文件存在时拒绝覆盖。

## 12. 阶段完成标准逐项检查

- Issue Schema、枚举和唯一 ID：PASS
- 确定性路由和优先级：PASS
- Requirement/Acceptance Criterion 追踪：PASS
- Generator 逐项回应：PASS
- 针对性复验生命周期：PASS
- 硬 PASS/FAIL 条件：PASS
- 状态最后提交和失败恢复：PASS
- 局部测试与完整回归：PASS
- 文档、模板和兼容说明：PASS

## 13. 阶段审核结论

`PASS`

## 14. 是否允许进入 F9.2

允许，自动进入 F9.2。

## 15. 是否同步到安装副本

否。按工作流要求，只有 F9.1、F9.2、F9.3 和最终系统审核全部 PASS 后才能同步。

## 16. 工作副本与安装副本差异

工作副本：
`C:\Users\28388\Desktop\ai-development-team-skill`

安装副本：
`C:\Users\28388\.codex\skills\ai-development-team-skill`

安装副本仍是更早的 v3 时代内容，未在本阶段修改。最终同步前必须重新生成完整
差异清单、备份安装副本并验证不存在无法合并的独立用户内容。
