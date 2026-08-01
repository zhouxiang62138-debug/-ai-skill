# F10R 测试报告（进行中）

最近完整命令：`python -m pytest -q`  
工作目录：`C:\Users\28388\Desktop\ai-development-team-skill`  
结果：381 passed，104 subtests passed，0 failed，0 error，0 skipped。

该结果不能证明全部 F10R 要求完成；尚缺少 Step CLI、rebind、完整 Tool 原子性和恢复故障注入覆盖。

## 2026-08-01 追加回归记录

- 命令：`python -m pytest -q`
- 工作目录：`C:\Users\28388\Desktop\ai-development-team-skill`
- 结果：392 passed，104 subtests passed，0 failed，0 error，0 skipped
- 覆盖增量：Patch 状态迁移、Lease 生命周期、Tool Attempt、迁移生命周期、显式 rebind。
- 限制：尚未以真实执行器覆盖所有 Tool timeout/副作用分支；Evaluation 与迁移中断的
  故障注入矩阵尚未完整。

## 2026-08-01 最终回归更新（本轮）

- 命令：`python -m pytest -q`
- 工作目录：`C:\Users\28388\Desktop\ai-development-team-skill`
- 结果：394 passed，104 subtests passed，0 failed，0 error，0 skipped
- 新增证据：真实多进程 Lease fencing 与 Tool `TIMED_OUT` 非成功终态。

## 2026-08-01 最终回归更新（等待状态与结果完整性）

- 命令：`python -m pytest -q`
- 工作目录：`C:\Users\28388\Desktop\ai-development-team-skill`
- 结果：398 passed，104 subtests passed，0 failed，0 error，0 skipped
- 新增证据：WAIT 状态不持有 Lease、`fail-step` 释放 Lease、Tool Result 提交前 Hash 验证。

## 2026-08-01 最终回归更新（恢复与迁移故障矩阵增量）

- 命令：`python -m pytest -q`
- 工作目录：`C:\Users\28388\Desktop\ai-development-team-skill`
- 结果：406 passed，104 subtests passed，0 failed，0 error，0 skipped
- 新增证据：真实 Evaluation state writer 异常、两类迁移中断、迁移重试、Rollback 记录、
  REQUESTED Tool Recovery、失败 Tool Result、实验模块隔离与文档一致性。
