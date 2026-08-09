# F10–F13 Production Readiness Review

> 文档状态：历史快照（2026-08-08），保留当时未就绪评审证据；当前结论已由
> `FINAL_PRODUCTION_READINESS_REVIEW.md` 取代。F10–F13 当前状态以
> `docs/RUNTIME_CAPABILITY_STATUS.md` 为准。

## 1. Executive Verdict

结论：**NOT_READY**。

真实 smoke project 在 First-Ask → Planner 的第一步 Runtime CAS 状态提交处被阻断，无法证明核心业务闭环可完成。F14 不启动。

## 2. End-to-End Workflow

- Project creation：PASS。创建了独立 `test_production_readiness_smoke_app`，schema v7，唯一 `project.yaml`；F10 DB 位于项目外；Skill 根目录没有 `project.yaml`。
- First-Ask：PARTIAL。原始请求、采访和 requirements snapshot 已生成。
- Planner：BLOCKED。First-Ask 状态迁移被 `ROLE_FIELD_OWNERSHIP_VIOLATION` 拒绝，project revision 仍为 0。
- Product approval gate：未到达真实 E2E；审批确定性测试通过。
- Plan approval gate：未到达真实 E2E；审批确定性测试通过。
- Generator：未到达真实 E2E。Local backend、ExecutionBroker 和路径策略测试通过，但没有批准链输入可执行。
- Evaluator：未到达真实 E2E。结构化 Evaluation/证据/返工测试通过，但项目未能进入 EVALUATING。
- FAIL → FIX → PASS：未到达真实 E2E；不能伪造 PASS/FAIL 路由。
- ACCEPTED：未到达。

## 3. Runtime

- v7 `project.yaml` 只保留 `session_id`、`control_plane_id`、revision；Control Plane 在 `C:\Users\28388\.ai-development-team\runtime\`。
- 同一 Session 的 pause/resume 通过，revision 未被错误递增。
- CAS 能拒绝越权状态提交，但当前生命周期字段权限缺失，导致正常状态迁移也被拒绝。
- 发现重复 `SESSION_CREATED` 审计事件：Orchestrator 启动已有 Session 时使用了不同幂等键；不影响当前阻断判定，但需要修复以保证审计幂等。

## 4. Execution

- `LocalCompatibilityEnvironment` 可经 `ExecutionBroker` 执行、读写受控路径并写入 Tool Call 历史。
- Generator 直接写 `project.yaml` 被 `PROJECT_STATE_WRITE_REQUIRES_CAS` 拒绝。
- Evaluator 写 `code/` 被 `EXECUTION_PATH_PROHIBITED` 拒绝。
- Windows 子进程树超时终止测试存在竞态：5 次单测中 1 次失败；完整回归当次通过。Local backend 不能宣称物理沙箱隔离。

## 5. Security

- role capability、路径策略、credential/network/external-tool policy 的既有测试通过。
- unknown capability 被拒；Evaluator 调用 Generator-only external tool 被拒。
- capability/external-tool DENY 事件进入 F10 Audit，且未发现 Secret 内容泄漏。
- `project.yaml`/`code/` 的路径拒绝能阻止写入，但当前没有独立的 path-deny Audit Event；这是审计完整性限制。

## 6. Context

- Planner、Generator、Evaluator 的 role-scoped Context、预算、provenance、hash 和跨项目边界测试通过。
- Incremental Context Resume 测试通过；Context 不读取 raw Control Plane 文件，也不包含 Secret。
- 在真实项目中因 CAS blocker 未能完成三个角色的连续 E2E Context 链路。

## 7. Evaluator/Rework

- F9 Issue Package、Evidence Manifest、Generator Response、迭代治理和五次上限的确定性测试通过。
- 真实项目没有到达 EVALUATING，因此未宣称真实 FAIL → FIX → PASS。

## 8. Change Request

- `test_change_request.py` 确定性测试通过。
- 真实项目未到达 ACCEPTED，无法诚实执行 `ACCEPTED → Change Request → Planner impact analysis → approval → Generator → regression → ACCEPTED`。

## 9. Recovery

- pause/resume：PASS，同一 Session、同一项目、重新签发 Lease。
- Snapshot/Restore：既有真实测试 `8 passed`，覆盖修改/新增内容恢复、hash verification、控制平面排除和损坏快照拒绝。
- 真实主流程的 Context Resume 依赖先解决 CAS 生命周期字段 blocker；独立 Context Resume 测试通过。

## 10. Multi-project Isolation

- `test_production_readiness_smoke_app` 与 `test_production_readiness_isolation_app` 使用不同 project ID、Session ID 和 Control Plane。
- Project B inspect Project A Session：拒绝。
- Project A inspect Project B Session：拒绝。
- Project A ExecutionContext 指向 Project B workspace：`EXECUTION_PROJECT_ROOT_MISMATCH`。

## 11. Known Limitations

- F11 Docker Sandbox 仍为 DEFERRED，原因是 Docker daemon unavailable。
- `LocalCompatibilityEnvironment` 不提供 physical sandbox isolation，不能把系统描述为 fully sandboxed autonomous coding system。
- Windows Local backend 的子进程树终止存在间歇性失败。
- Path-deny 没有统一的 F10 Audit Event。
- Orchestrator 对已有 Session 的创建事件幂等性需要修复。

## 12. Test Evidence

- 完整命令：`python -m pytest -q`
- 结果：`505 passed, 5 skipped, 104 subtests passed`。
- Snapshot/Restore：`8 passed`。
- 相关 Runtime/Execution/Security/Context/Evaluation/Change Request 子集：`266 passed, 1 failed, 55 subtests passed`；失败为 Windows 子进程树终止，重复 5 次出现 1 次失败。
- 真实项目报告：`C:\Users\28388\Desktop\ai-projects\archive\test_production_readiness_smoke_app\TEST_REPORT.md`。
- 隔离项目报告：`C:\Users\28388\Desktop\ai-projects\archive\test_production_readiness_isolation_app\TEST_REPORT.md`。
- F10 最终 Session 投影：status 仍为 `INTAKE`，revision `0`；完整事件保存在外部 SQLite。

## 13. Final Recommendation

- 当前不允许开始真实项目开发。
- 当前不允许开始 F14。
- 先修复并回归验证角色生命周期字段的 CAS ownership；之后重新跑本报告要求的完整真实 E2E，尤其是 FAIL → FIX → PASS、Change Request 和 ACCEPTED 后恢复链路。
