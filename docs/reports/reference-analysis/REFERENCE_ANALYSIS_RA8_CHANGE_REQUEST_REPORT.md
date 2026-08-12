# RA8 Change Request Reference Integration 阶段报告

## 阶段

RA8 — Change Request Reference Integration

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段新增 `scripts/reference_change_request.py` 和 `reference_change_binding_v1.schema.json`，把 Reference IDs、Synthesis 版本、CR 专属变更范围、批准引用和 baseline manifest 绑定到已有活动 Change Request。该路径不会重新执行 First-Ask，不会把 CR 当成 new project，也不会覆盖原项目 Reference 历史。

## 约束

- 绑定 ID 使用 `CRREF-*`，记录位于 `change_requests/<CR>/references/`。
- 绑定前检查项目存在活动 CR、project_id 一致、CR 未进入终态。
- 新绑定通过 `supersedes` 指向上一条活动 binding；撤销通过追加 `REVOKED` 快照完成。
- 原始 Reference、旧 Synthesis、旧 Binding 和 baseline 均保持不变。
- Reference Analysis 完成不等于 Change Approval；`change_scope_approval_ref` 必须显式提供，用户批准仍由既有 CR 流程负责。

## 验证证据

- RA8 专项测试：`2 passed`
- 历史保留与撤销：通过
- 新项目初始化/无活动 CR 拒绝：通过
- RA7-B 至 RA7-F、RA8–RA10 与既有仓库最终全量回归：`723 passed, 5 skipped, 113 subtests passed`

## 已知限制

1. RA8 只实现 Reference Binding 协议和历史保护，Planner impact analysis、Generator handoff、Evaluator conformance 仍复用现有 CR 流程，需要在真实产品项目中由对应角色完成批准链。
2. 本阶段不自动更改 project.yaml，不自动审批变更范围。

## Gate 决策

CONTINUE — CR 专属 Reference 历史、撤销和 baseline 保护已具备可复现证据，进入 RA9 对抗测试。
