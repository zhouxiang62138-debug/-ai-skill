# 已完成项目 Change Request 工作流

## 目标与入口

当用户提供一个已经 `ACCEPTED` 或 `ARCHIVED` 的项目及新的修改意见时，继续读取
原项目、原 Plan、原 Evaluation、原代码和历史工件，不重新初始化项目，也不重新
执行 First-Ask 新项目流程。

Change Request 是 `change_request` Module，不是第四个 Agent。系统仍只有
Planner、Generator、Evaluator；`project.yaml` 仍是唯一项目状态源。

```text
修改已经完成的项目：

项目路径：
C:\Users\28388\Desktop\ai-projects\expense-app

修改意见：
1. 简化首页
2. 增加 Excel 导出
3. 修复编辑金额后显示不更新

请读取原项目，不要新建项目。
先生成影响分析，在我批准前不要改代码。
```

预期入口回复应说明 CR ID、稳定版本、Plan 版本、项目状态、初步分类，以及“批准
前不会修改代码”。

## 状态与文件协议

```text
ACCEPTED / ARCHIVED
  → CHANGE_REQUESTED
  → WAITING_FOR_CHANGE_APPROVAL
  → IMPLEMENTING
  → EVALUATING
  → RELEASE_READY
  → ACCEPTED
```

取消或全部拒绝恢复创建请求前的 `ACCEPTED` / `ARCHIVED`。Release 写入失败时
保持 `RELEASE_READY`，不能错误进入 `ACCEPTED`。

```text
change_requests/
  CR-0001.yaml
  CR-0001/
    events/event-001.yaml
    impact-analysis-001.yaml
    impact-analysis-001.md
    approvals/approval-001.yaml
    baseline/manifest.yaml
    handoffs/handoff-001.yaml
    evaluations/evaluation-001.yaml
releases/
  release-1.0.0.yaml
  release-1.1.0.yaml
  rollbacks/rollback-001.yaml
memory/plans/
  plan-001.md
  plan-002.md
memory/migrations/
  change-request-migration-001.yaml
```

原始请求、事件、批准、Plan、Handoff、Evaluation、Release 和回滚记录都是
追加式工件，禁止覆盖。

## Change Type 与 Planner

正式类型位于 `config/change_request.yaml`：`bug_fix`、`ui_improvement`、
`usability_improvement`、`content_change`、`configuration_change`、
`new_feature`、`behavior_change`、`performance_improvement`、
`security_change`、`compatibility_change`、`data_migration`、
`scope_change`、`major_change`。

确定性关键词只做初步分类。Planner 必须逐项说明范围、数据模型、API、兼容性、
迁移、依赖、Requirement、Acceptance Criterion、组件、回归和风险。新功能必须
新增 Requirement 与 Acceptance Criterion；Major Change 必须明确高风险。

批准支持全部、部分和拒绝，必须逐项决定。未批准前没有 Generator 权限。部分
批准只把获批项写入新 Plan，旧 Plan 不覆盖。

## Baseline、Generator 与恢复

Generator 开始前记录原 Release、Plan、Evaluation、Git commit（可用时）和
`code/`、`tests/` 的 SHA-256 Manifest。只允许项目内批准范围，禁止改写历史
Plan、原始反馈、Release、Evaluation、Requirement、Proposal、Evaluation
Profile 或验收阈值。

Handoff 必须逐项回应所有获批项，记录真实修改文件与哈希、自测、限制和回滚。
重试使用新的 `handoff-<nnn>.yaml`。

```powershell
python scripts/change_request.py recover <project-root>
```

恢复命令只核对 `project.yaml` 与事件链并给出 PASS/BLOCKED，不静默删除历史。

## Evaluator 与迭代

Evaluator 同时验证每个获批 Change Item 和原核心功能回归。每项变更必须关联
Requirement、Acceptance Criterion 与证据；回归必须有 ID 和证据。新变更通过
但回归失败时仍不能 PASS。

只有返回 Generator 的 FAIL 增加当前请求内
`change_context.evaluation_iteration`。BLOCKED、Planner 路由和等待不增加。
第 5 次失败进入 `WAITING_FOR_USER`。新请求从 0 开始。

## Release、回滚与旧项目迁移

项目有版本策略时优先使用；否则 Bug Fix 用 Patch，新功能用 Minor，Major Change
用 Major。旧项目没有 Release 记录时，首次变更补建明确标注的 `BASELINE`，并
声明它是迁移记录，不代表历史上真实存在。

回滚接口只有在调用方真实恢复代码/数据并提供回归证据后才更新 Release 指针；
写记录不能冒充真实恢复。旧 Release 永不删除。

v3/v4/v5 的完成项目在第一次请求前先校验、追加备份原 `project.yaml`、写迁移
记录、最小迁移到 v6 并复验。不伪造 Requirement、AC、Release 或历史批准。

## 并发与常见错误

第一版每个项目只允许一个 `active_change_request`，不自动并行或静默合并。

- “只有 ACCEPTED 或 ARCHIVED”：原阶段未完成，先完成原门禁。
- “已存在 active_change_request”：先完成、取消或人工处理当前请求。
- “路径逃逸”：禁止绝对路径、`..` 或项目根目录外引用。
- “未批准 Change Item”：修正 Generator 实施范围。
- “Handoff 后文件哈希变化”：重新交接并解释变化。
- “Release 已存在”：禁止覆盖，检查重复执行或版本冲突。

## 测试

```powershell
<python> -m unittest tests.test_change_request -v
<python> -m unittest discover -s tests
<python> -m py_compile scripts/change_request.py scripts/project_state.py
git diff --check
```
