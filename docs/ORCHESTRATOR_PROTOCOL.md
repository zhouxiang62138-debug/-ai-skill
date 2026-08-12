# Orchestrator 协议

## 职责

1. 定位项目和 Session DB。
2. 读取并验证 project schema。
3. 获取、续约、释放或接管过期 Worker Lease。
4. 根据 `active_module`、`status`、`next_role` 确定性选择目标。
5. 记录角色/工具生命周期 Event。
6. 通过 CAS 提交角色产生的候选业务状态。
7. 创建 Checkpoint 并执行恢复。

## 禁止事项

Orchestrator 不得生成产品内容、编写业务代码、修改需求、判断 PASS/FAIL、越过
用户批准或引入新 Agent。WAIT 状态只返回等待原因。

## CLI

```text
python -m runtime.cli begin-step <project_root>
python -m runtime.cli inspect-step <session_id> --project-root <project_root>
python -m runtime.cli commit-step <session_id> --project-root <project_root> --run-id <id> --lease-token <token> --result <result.json>
python -m runtime.cli fail-step <session_id> --project-root <project_root> --run-id <id> --result <reason.json>
python -m runtime.cli resume <session_id> --project-root <project_root>
python -m runtime.cli pause <session_id> --project-root <project_root>
python -m runtime.cli recover <session_id> --project-root <project_root>
```

Codex 不由 Python 自动调用模型：先用 `begin-step` 取得角色、Run ID、Lease Token 和
expected revision，完成角色工作后由正式 Phase Runner 生成 Attestation，再把结构化结果
交给 `commit-step`。结果必须包含 `source_status`、`target_status`、业务
`changed_fields`、`expected_revision`、`idempotency_key` 与 `attestation_id`；完整
`next_state` 不被接受。`status`、`next_role` 和 `active_module` 不属于业务
`changed_fields`，由 Runtime 按目标状态路由自动派生。暂停和恢复后都会撤销旧 Lease，
恢复步骤会签发新的 Lease Token。

## Role Execution Request

正式 Role Run 先由 Context Builder 生成 Role-scoped Manifest，再由
`RoleExecutionBroker` 创建新的 Role Execution。它会记录：

```text
role_execution_id
role_run_id
execution_mode
host_thread_id（仅真实 Child Thread）
invocation_id
context_manifest_id
workspace_binding
source_revision
```

首选模式是 `CHILD_THREAD`。宿主没有可验证的 Child Thread 能力时，实际模式必须
变为 `FRESH_INVOCATION`，并追加 `ROLE_EXECUTION_FALLBACK`；不允许用随机字符串
伪造 thread ID。提交前 Phase Attestation 必须绑定同一 Role Execution、Invocation、
Context Manifest、source revision 和 execution mode。

进入等待态前不得保留活动 Role Execution；暂停会安全取消活动 execution、撤销
Lease 并保留 Durable State。

## E1 Evaluator 独立性边界

Orchestrator 保持三个核心 Agent，不创建 Reviewer 或 QA Agent。Role Run 进入
Evaluator 时，PhaseRunner 先由 Deterministic Context Builder 生成
`EVALUATOR_INDEPENDENT` Manifest，再创建 Fresh Model Invocation；Evaluator 的
结构化结果必须经过 E1 Verifier、Evidence Provenance 和当前 revision/code snapshot
校验。模型输出 `PASS` 不能绕过 Runtime Gate。
