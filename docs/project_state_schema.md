# 项目状态 Schema 与兼容协议

## 当前版本

新项目使用 `schema_version: 4`。v4 只扩展项目状态的数据结构和确定性校验，
不会在 F4、F5、F6 工作流接入完成前自行触发产品探索或 Plan 批准流程。

v4 新增：

- 独立产品规格状态与活动指针。
- 正式 Plan 版本、审核状态、获批指针和批准记录。
- 产品探索反馈记录指针。
- `WAITING_FOR_PLAN_REVIEW` 状态的数据约束。

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

迁移实现必须另行记录 `schema_migration_record`。F3 不执行具体项目迁移。

## 校验命令

```powershell
python scripts/project_state.py <项目根目录>\project.yaml
python scripts/project_state.py <项目根目录>\project.yaml --check-paths
python scripts/project_state.py <项目根目录>\project.yaml --migration-assessment
```

校验器使用 Python 标准库，不要求 PyYAML 或 jsonschema。它只接受
`project.yaml` 所需的安全 YAML 子集，拒绝重复键、Tab 缩进、YAML 锚点、
别名和自定义标签。
