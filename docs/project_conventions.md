# 项目约定

## 已完成项目变更目录

后续修改使用 `change_requests/CR-<nnnn>.yaml` 和同名子目录保存追加式事件、
影响分析、批准、基线、Handoff 与 Evaluation。Release 使用
`releases/release-<version>.yaml`，回滚使用
`releases/rollbacks/rollback-<nnn>.yaml`。历史工件不得覆盖或删除。

## F9 验收目录

```text
evaluation/
  reports/
    evaluation-<nnn>.md
  issues/
    evaluation-<nnn>.yaml
  evidence/
    evaluation-<nnn>/
      manifest.yaml
      commands/
  decisions/
    decision-summary-<nnn>.yaml
  .transactions/
    evaluation-<nnn>/
      journal.json
memory/
  handoffs/
    responses/
      evaluation-<nnn>-response.yaml
  migrations/
    migration-<nnn>.json
```

以上历史工件均追加创建。Issue、Evidence、Response、决策摘要和迁移记录不得覆盖。
`.transactions` 是恢复日志，不是 PASS 证据；只有 `COMMITTED` 事务可作为完整
Evaluation 来源。

每个项目位于 `C:\Users\28388\Desktop\ai-projects\<project_id>`，根目录只能有一个 `project.yaml`。禁止在 `memory/` 创建第二个状态文件。

原始请求、采访和需求快照采用追加编号：`request-001.md`、`interview-001.md`、`requirements_v001.yaml`。项目计划、交接和报告采用追加编号：`plan-001.md`、`handoff-001.md`、`evaluation-001.md`。产品方案采用三位追加版本：`product_proposal_v001.md`、`product_proposal_v002.md`。产品探索反馈采用 `design-feedback-001.md`，明确选择采用 `design-selection-001.md`，设计预览轮次采用 `artifacts/design_previews/round_001/`。历史工件不得覆盖。项目之间默认隔离，除非用户明确授权复用。

正式产品规格采用 `product_spec_v001.md`；产品批准、Plan 批准、批准撤销和
实施后变更分别采用 `product-approval-001.md`、`plan-approval-001.md`、
`approval-revocation-001.md` 和 `change-request-001.md`。每次修订都创建
严格递增的新版本或新记录，不覆盖旧工件。

项目中：`code/` 保存生产代码；`memory/requirements/` 由 First-Ask Intake
写入、Planner 只读；`memory/proposals/` 保存候选与整合产品方案；
`memory/specifications/` 保存获批方案派生的正式产品规格；`memory/plans/`
保存待审核及获批开发 Plan；`memory/decisions/` 保存产品批准、Plan 批准、
撤销和变更记录；`evaluation/` 保存验收报告；`artifacts/` 保存设计预览、
测试日志、截图和构建物。Planner 只能在 `artifacts/design_previews/`
创建设计验证工件，不得把预览写入 `code/`。

每个设计预览轮次必须恰好包含 `concept_01`、`concept_02` 和 `concept_03`。
每个概念目录包含 `concept.md`、`preview.html` 和 `preview.css`。三个概念必须是
产品定位、特色功能、主要用户路径或信息架构存在实质差异的完整产品路线，
不得只换色。新反馈需要重新比较方向时，创建新的轮次目录，禁止覆盖旧轮次。

探索生成中断时，可以补齐当前轮次尚未创建的文件，但不得覆盖已有非空文件。
已有工件格式错误或路线差异不足时，保留该轮次并创建下一轮。
