# 项目状态 Schema 与兼容协议

## Change Request 字段

- `active_change_request`：当前唯一 `CR-<nnnn>`，无活动请求时为 `null`。
- `change_cycle`：累计外部修改轮次。
- `change_context.previous_project_status`：取消时恢复的稳定状态。
- `change_context.evaluation_iteration`：当前请求内返工次数，必须与
  `current_iteration` 一致。
- `change_impact_analysis`、`change_approval_record`、`change_baseline`：
  当前请求来源链。
- `approved_change_items`：Generator 唯一可实施的 Change Item。
- `release_version`、`current_release`：当前稳定发布版本及记录。

新增状态为 `CHANGE_REQUESTED`、`WAITING_FOR_CHANGE_APPROVAL` 和
`RELEASE_READY`。没有活动请求时 `change_context` 必须为 `null`。

## 当前版本

## v6：结构化验收与受控循环

新项目使用 `schema_version: 6`。v6 在 v4 产品批准链和 v5 Skill Maintenance
目标边界上，新增：

- `iteration_sequence`
- `automatic_retry_allowed`
- `last_issue_package`
- `last_generator_response`
- `evidence_manifest`
- `iteration_metrics`
- `retry_history`
- `routing_disagreements`
- `escalation_record`
- `decision_summary_record`

`current_iteration` 达到 5 或存在升级记录时，`automatic_retry_allowed` 必须为
`false`，状态不得继续进入自动 `IMPLEMENTING`。只有新的正式 Plan 和新的 Plan
批准记录可以通过确定性治理函数开启新序列并将轮次归零。

## F8.5：Skill Maintenance Project

`schema_version: 5` 新增 `project_type: skill_maintenance`。它只允许项目状态位于
独立项目目录；`targets.working_repository` 是唯一可写外部目标，
`targets.installed_repository` 在最终同步前后都不能由 Generator 直接写入。
`scripts/skill_maintenance.py` 会规范化并检查路径、拒绝未声明目标和路径穿越，
并以安装副本基线清单识别未知修改。同步要求 `status: ACCEPTED`、
`final_evaluation_status: PASS`、排除规则和可恢复备份；`.git` 元数据、项目状态、
memory、evaluation、logs、archive 与缓存不得同步。

旧 `schema_version: 4` 项目仍可读取。v4 扩展项目状态的数据结构和确定性校验，
并由 F4、F5、F6 工作流分别接入产品探索、反馈处理和双重批准门禁。

v4 新增：

- 独立产品规格状态与活动指针。
- 正式 Plan 版本、审核状态、获批指针和批准记录。
- 产品探索反馈记录指针。
- `WAITING_FOR_PLAN_REVIEW` 状态的数据约束。
- 产品探索触发原因、生成尝试次数和错误记录。

F4 使用 `scripts/exploration.py` 校验同一轮恰好三套完整产品路线、必需文件、
预览标记和路线差异，并给出中断恢复动作。探索生成最多自动尝试两次。

F5 使用 `scripts/feedback.py` 把用户反馈分类为单选、修改、混搭、全部否定、
继续讨论、恢复、含糊或冲突，并生成确定性候选状态。每次反馈和明确选择使用
独立追加记录；产品方案版本必须严格递增，选择不构成产品批准。

F6 使用 `scripts/approval.py` 区分产品批准与 Plan 批准。产品批准只生成
正式产品规格和待审核 Plan，并进入 `WAITING_FOR_PLAN_REVIEW`；只有 Plan 被
单独明确批准，且产品批准记录、正式规格、获批 Plan、Plan 批准记录及设计
来源链均校验通过，才可进入 `APPROVED_FOR_IMPLEMENTATION`。实施前撤销使用
追加式撤销记录；实施开始后的范围变化必须创建变更请求并暂停等待用户决策。

## 唯一状态源

项目根目录的 `project.yaml` 仍是唯一状态源。JSON Schema 是结构契约，
不保存项目状态；验证工具也不得创建 `memory/project.yaml`。

## v3 兼容

`scripts/project_state.py` 同时读取并校验 schema v3 和 v4：

- v3 默认只读，不会因为加载而写回或迁移。
- 规划和设计阶段的 v3 项目可在后续迁移阶段按需迁移。
- `APPROVED_FOR_IMPLEMENTATION`、`PLANNING_COMPLETE`、`IMPLEMENTING`、
  `EVALUATING`、`ACCEPTED` 状态需要人工审核，不能自动补造 Plan 批准记录。
- `ARCHIVED` 项目不得迁移。

## 写入规则

调用 `write_project_state_atomic` 时：

1. 先执行结构和语义校验。
2. 在项目根目录创建临时文件。
3. 刷新内容后原子替换根级 `project.yaml`。
4. 校验失败时拒绝写入，不改变原文件。

迁移必须另行记录 `schema_migration_record`，并使用下述 v6 迁移工具。

## v6 迁移工具

`scripts/project_migration.py` 支持：

```text
check
preview
migrate
verify
rollback
```

迁移前必须指定不存在的备份路径；工具拒绝覆盖现有备份。迁移记录追加写入
`memory/migrations/`。重复迁移 v6 项目是幂等操作。归档项目保持只读；处于实施、
验收或已验收状态的 v3 项目要求人工复核来源链。回滚前还会保存当前 v6 状态，
迁移前后备份均不删除。

## 校验命令

```powershell
python scripts/project_state.py <项目根目录>\project.yaml
python scripts/project_state.py <项目根目录>\project.yaml --check-paths
python scripts/project_state.py <项目根目录>\project.yaml --migration-assessment
```

```powershell
python scripts/project_migration.py check <项目根目录>\project.yaml
python scripts/project_migration.py preview <项目根目录>\project.yaml
python scripts/project_migration.py migrate <项目根目录>\project.yaml --backup <备份路径>
python scripts/project_migration.py verify <项目根目录>\project.yaml
python scripts/project_migration.py rollback <项目根目录>\project.yaml --backup <迁移前备份> --pre-rollback-backup <回滚前备份>
```

校验器使用 Python 标准库，不要求 PyYAML 或 jsonschema。它只接受
`project.yaml` 所需的安全 YAML 子集，拒绝重复键、Tab 缩进、YAML 锚点、
别名和自定义标签。
