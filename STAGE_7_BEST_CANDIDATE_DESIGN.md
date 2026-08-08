# Stage 7：Best Validated Candidate 设计

## 目标

把“最新实现”与“最佳已验证实现”分开。每轮 Evaluator 追加 Candidate 记录，Runtime
根据完整验证条件选择最佳版本；后续退化不能覆盖此前最佳 Candidate。

## Candidate 记录

记录位于 `evaluation/candidates/candidate-<nnn>.yaml`，保存 Snapshot、Evaluation、
Project Revision、分数、阻塞/严重问题、回归、Feature Completeness、Browser Acceptance
和验证时间。记录严格递增且不可覆盖。

只有同时满足以下条件的 Candidate 才可成为最佳：

- 没有 blocking/critical issue。
- Regression 为 `PASS`。
- Feature Completeness 为 `PASS` 或达到当前 8.0 门槛的分数。
- Browser Acceptance 为 `PASS` 或非 Web 项目的 `SKIPPED`。

候选集合中按分数降序、Candidate 编号升序确定最佳；分数较低的新 Candidate 不会
替换旧最佳。

## Restore 权限

Evaluator 只能追加 `recommend_restore_candidate` 推荐记录，且记录明确
`no_restore_performed: true`。真正恢复只能由 Runtime/Snapshot Service 使用已有
Snapshot 能力执行；Candidate 选择器不直接修改工作区。
