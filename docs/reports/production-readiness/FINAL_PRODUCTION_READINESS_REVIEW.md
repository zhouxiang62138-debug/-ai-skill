# Final Production Readiness Review

## 1. Executive Verdict

**FINAL PRODUCTION READINESS: READY_WITH_LIMITATIONS**

核心真实项目流程、Runtime、Execution、Security、Context、Recovery、Auditability 和 Change Request 均有通过的真实测试证据。当前唯一明确的非阻塞限制是 Docker Sandbox 仍为 `DEFERRED`；`LocalCompatibilityEnvironment` 提供受控本机执行，但不提供物理 Sandbox 隔离。

本结论适用于用户自己控制的本机项目开发，不等同于可安全执行任意不可信代码的 fully sandboxed autonomous coding system。

## 2. Verified Business Workflows

- 新项目闭环：First-Ask Intake Module 通过 F10 CAS 进入 Planner；随后完成 Product Proposal、独立 Product Approval、Formal Product Spec、Development Plan、独立 Plan Approval、Generator、Evaluator 和 `ACCEPTED`。
- Product Approval 与 Plan Approval 是两个独立 Gate。Product Approval 后仍停留在 `WAITING_FOR_PLAN_REVIEW`，只有 Plan Approval 才进入 `APPROVED_FOR_IMPLEMENTATION`。
- 真实 FAIL → FIX → PASS：Stage 3 E2E 实际制造实现缺陷，由 Evaluator 生成 issue package 并路由回 Generator；修复后重新执行验证并进入 `ACCEPTED`。`current_iteration` 从 0 增加到 1，自动返工上限保持为 5。
- 真实 `ACCEPTED → Change Request → ACCEPTED`：Stage 4B E2E 覆盖 CR、Planner impact analysis、scope approval、Change Plan、Generator 增量实现、实际回归测试、Evaluator 和最终 Release CAS。

证据：`tests/test_p0_workflow_cas_ownership.py`、`tests/test_stage2_core_e2e.py`、`tests/test_stage3_rework_e2e.py`、`tests/test_stage4b_change_request_e2e.py`。

## 3. Runtime & Recovery

- F10 使用项目外部 Durable Control Plane 和 SessionStore；`project.yaml` 只保存 managed-project business state 与 runtime projection。
- Skill repo 根目录没有伪造的 `project.yaml`，Skill repo 与 managed project 保持隔离。
- Revision、Worker Lease/Fencing、CAS、Idempotency、Durable Event、Tool Call lifecycle 和 crash recovery 均有测试覆盖。
- Pause/Resume 能恢复 Session、Lease 和 Context Manifest；Snapshot/Restore 完成 workspace hash 校验，并能在 workspace 损坏后继续执行。
- v3–v6 legacy migration 保持首次读取只读，并通过显式 migration/verify 流程进入 v7。

## 4. Execution

- Generator 和 Evaluator 均通过 `ExecutionBroker` 使用 `LocalCompatibilityEnvironment`；argv 为参数数组，`shell=False`，输出受 bounded capture 限制。
- Windows timeout cleanup 使用 Windows Job Object、`TerminateJobObject`、有限等待和 reap；parent、child、grandchild、already-exited race、幂等 terminate 和 20 轮 stress 均通过。
- Tool timeout 仍返回 `TIMED_OUT`，不会因 cleanup 成功伪装成 Success。
- Snapshot/Restore 和 Recovery 均不依赖 Backend 直接访问 SessionStore。

## 5. Security

`DENY BY DEFAULT` 仍成立：

- Generator direct `project.yaml` write：DENY。
- Evaluator write `code/`：DENY。
- Stale revision、stale lease、非法 transition 和未拥有字段：DENY。
- Parent/path escape、symlink escape、Windows Junction escape、Control Plane raw access：DENY。
- Unknown capability 和未授权 external tool：DENY。
- Path DENY 按 denial 去重为一个 authoritative `PATH_ACCESS_DENIED` event；Capability DENY 不重复生成 Path DENY。
- Event、Context、Tool Result、Backend 和 project files 未发现 Secret 或文件正文泄漏。
- P0/P3 没有扩大 Planner、Generator、Evaluator 的任意字段权限，也没有引入 CAS bypass。

证据：`tests/test_p0_workflow_cas_ownership.py`、`tests/test_p2_path_deny_audit.py`、`tests/test_p3_change_request_runtime.py`、`tests/test_capability_policy.py`、`tests/test_credential_broker.py`、`tests/test_network_tool_policy.py`。

## 6. Context

- Planner、Generator、Evaluator Context 实际不同，并按 role、workflow state、project 和 session 进行约束。
- Context budget、REQUIRED protected source、inline/reference 规则、source provenance、content hash、context hash 和 policy revalidation 均通过。
- Incremental Resume 可用；等价的 Full Build 与 Incremental reconstructed context 的最终 `context_hash` 相同。
- Context 不读取 raw Control Plane 文件，不包含 Secret，也不跨 project/session 复用。

证据：`tests/test_formal_context_builder.py`、`tests/test_context_builder.py`、`tests/test_context_budget.py`、`tests/test_context_resume.py`。

## 7. Change Request

Stage 4B 保留了同一项目、原 Product Spec source chain、原 Plan、previous evaluation 和原有 acceptance criteria；新增 Change Plan 使用追加式 `plan-002.md`，不覆盖 `plan-001.md`，并加入新的 acceptance criterion 与 regression scope。

Generator 只修改允许的 `code/` 和 `tests/`，生成 handoff/evidence；Evaluator 读取旧、新验收标准和 Generator evidence，执行实际 regression，创建 evaluation report，不能写 code 或修改 Plan。最终 Release 通过 F10 CAS 返回 `ACCEPTED`，历史记录仍可重建完整 CR 生命周期。

## 8. Auditability

外部 SessionStore 的 Durable history 能解释：

```text
project/session created
First-Ask → Planner
product approval
plan approval
Generator execution
Evaluator FAIL
Generator fix
Evaluator PASS
ACCEPTED
Change Request
impact analysis
scope approval
change implementation
regression
final ACCEPTED
```

Stage 4B 额外验证了 Change Request stage events、Tool Call history、Path DENY audit 和 SessionStore reopen 后的持久性。解释上述关键状态不依赖聊天记忆。

## 9. Operator Usability

- 普通用户可以通过 Skill 对话提交需求并等待 Product/Plan Gate；不需要手动编辑 `project.yaml`、SessionStore、Context manifest 或执行 CAS。
- First-Ask、Orchestrator、ExecutionBroker、Security Boundary、Credential Broker、Context Builder 和 Session Runtime 都是基础设施或 Module，不构成第四个 Agent。
- 核心 Agent 仍只有 Planner、Generator、Evaluator，并由 Runtime 按状态自动路由。
- 当前证据主要通过真实 Python E2E harness 验证内部闭环；这不改变用户侧由 Skill 协调内部 Runtime 的使用方式。

## 10. Known Limitations

- F11 Docker Sandbox：`DEFERRED`，原因是 Docker daemon unavailable。
- `LocalCompatibilityEnvironment` 没有物理进程/filesystem sandbox 隔离；它是 managed local execution with security controls。
- 因此不应把系统宣传为 fully sandboxed autonomous coding system。
- Docker 在“用户自己控制的本机、受信任项目”定位下不是开始本地真实项目的前置条件；若目标变为执行任意不可信代码，则 Docker 或等效物理隔离会成为前置条件。

## 11. Test Evidence

最终执行：

```text
python -m pytest -q
541 passed, 5 skipped, 104 subtests passed
```

无失败、无新增 skip、无 xfail。5 个 skipped 全部属于 Docker daemon unavailable / F11 Docker deferred，不代表 Docker 功能通过。

## 12. Final Recommendation

- **START REAL PROJECTS: YES**，限定为用户自己控制的本机真实软件项目，并接受本机执行没有物理 Sandbox 隔离。
- **START F14: NO**。Final Review 没有发现必须通过新基础设施阶段才能解除的 blocker；下一步应记录真实项目使用摩擦，再根据真实问题决定未来优化。
- **DOCKER REQUIRED BEFORE LOCAL USE: NO**。Docker 仍应保持 `DEFERRED`，但不是当前本地受信任项目开发的阻塞条件。

