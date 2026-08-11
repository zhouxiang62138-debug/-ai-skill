# E1 Evaluator Independence Hardening：架构设计

## 设计目标

E1 的目标是让同一个 Codex 用户窗口仍可自动完成 Planner → Generator → Evaluator，但把 Evaluator 变成一个新的、角色限定的 Runtime Model Invocation，并让最终 PASS 必须经过独立证据和确定性 Gate。

E1 不增加第四个 Agent，也不要求使用不同模型。Generator 与 Evaluator 可以使用同一个底层模型，但必须使用不同 Invocation、不同 Context Manifest 和独立重现步骤。

## E1 边界

```text
F11 Execution Runtime  -> 提供可审计的执行与 Snapshot
F12 Security Runtime   -> 提供 Capability / Credential / Network / Path Boundary
F13 Context Builder    -> 提供预算化、角色化、可恢复 Context
E1                    -> 定义验收独立性、证据 provenance、重现策略和 PASS Gate
```

E1 是验收可信度层，不是 F11/F12/F13 的子阶段。

## 1. Model Invocation

Durable Session 可以包含多次 Invocation；Invocation 不能等同于 Session。

正式 Invocation 元数据至少包括：

```yaml
session_id: session-...
invocation_id: model-invocation-...
role: planner | generator | evaluator
source_revision: 12
context_manifest_id: context-...
started_at: 2026-08-11T...
completed_at: 2026-08-11T...
model_id: default | unknown
capability_profile: FULL | STANDARD | unknown
fresh_context_required: true
context_isolation: RUNTIME_CONTEXT_ONLY | HOST_AND_RUNTIME
```

Evaluator 的 `fresh_context_required` 必须为 `true`。Runtime 在创建 Evaluator Invocation 前拒绝同一 Session 中仍处于 ACTIVE 的上一 Invocation。失败恢复会先结束中断 Invocation，再由新的 Role Run 创建新的 Evaluator Invocation。

Runtime 可以保证自己的 Context 输入是新的、结构化的、角色限定的；如果宿主 API 不能控制外层 Codex 对话历史，则记录 `context_isolation: RUNTIME_CONTEXT_ONLY`，不能把它伪装成 Host Conversation Isolation。

## 2. Evaluation Context Manifest

Evaluator Context 由 F13 Deterministic Context Builder 生成，但 E1 规定它的语义：

### 允许的最小来源

- 当前 `project.yaml` 权威状态；
- approved requirements；
- approved product spec；
- approved development plan；
- evaluation profile；
- Acceptance Criteria 及其批准来源链；
- changed files / implementation diff 的受控入口；
- Generator handoff 的事实性定位字段；
- 当前 revision/code snapshot/environment；
- 尚未解决的 evaluation issues；
- 必要的批准 reference/conformance requirements。

### 明确排除的来源

- Generator 完整聊天历史；
- Generator 推理过程；
- Generator 自我评价和“应该通过”等结论性叙述；
- 上一个 Invocation 的自由文本上下文；
- 未批准的 Plan、需求、设计方向或原始 Reference。

当前实现通过 evaluator-specific exclusion policy 过滤 `last_generator_response` 和聊天/推理类额外引用；Generator handoff 只允许作为定位输入，不能自动成为 PASS Evidence。

Context Manifest 对 Evaluator 标记：

```yaml
context_type: EVALUATOR_INDEPENDENT
excluded_sources:
  - generator_chat_history
  - generator_reasoning
  - generator_self_assessment
  - previous_invocation_freeform_history
```

## 3. Evidence Provenance

E1 固定四类来源：

```text
GENERATOR_PROVIDED
EVALUATOR_REPRODUCED
RUNTIME_VERIFIED
EXTERNAL
```

Generator 运行的测试可作为 implementation feedback、handoff 或 candidate evidence，但不能单独支撑 required/critical Acceptance Criterion 的最终 PASS。

Evaluator/Runtime Evidence 必须绑定：

```yaml
evaluation_id: evaluation-001
acceptance_criterion_id: AC-001
provenance: EVALUATOR_REPRODUCED
tool_call_id: tool-call-...
attempt_id: attempt-...
command: [python, -m, pytest]
environment: { ... }
result_hash: <sha256>
timestamp: 2026-08-11T...
project_revision: 12
code_snapshot_hash: <sha256>
```

对 required/critical Criteria，至少需要 `EVALUATOR_REPRODUCED` 或 `RUNTIME_VERIFIED`。Evidence 的 revision 或 code snapshot 不匹配当前 Evaluator Context 时，确定性 Gate 失败。

## 4. Independent Reproduction Policy

`config/evaluation_independence.yaml` 是 E1 的唯一策略入口：

```yaml
fresh_invocation_required: true
inherit_previous_invocation_history: false
generator_claims_are_evidence: false
required_evidence_provenance:
  critical: [EVALUATOR_REPRODUCED, RUNTIME_VERIFIED]
reexecute:
  build: true
  required_tests: true
  browser_required_scenarios: true
  regression: true
evaluator_write_code: false
```

Evaluator 必须把 Generator Response 转换成 `CLAIM → VERIFY → RESULT`。Generator 的 `FIXED` 只表明 claim，不能改变 Issue status 或 Evaluation result。

## 5. Deterministic PASS Gate

Runtime 只有在以下条件全部满足时才允许 Evaluator 的 PASS transition：

1. Evaluator Invocation 是新的 Invocation，且 `fresh_context_required=true`；
2. Evaluator Context Manifest 为 `EVALUATOR_INDEPENDENT`，排除项没有进入模型输入；
3. required Acceptance Criteria 全部映射；
4. required/critical Evidence provenance 满足最低要求；
5. build、required tests、required browser scenarios 和 regression 按 Profile 要求重现；
6. blocker 与 critical Issue 均为 0；
7. protected artifact 与验收前快照一致；
8. Evidence 的 project revision 与 code snapshot 与当前 Context 一致；
9. 没有 unresolved prior blocking issue；
10. Evaluator 没有写入 code、Plan、Requirements、Evaluation Rule 或 threshold；
11. 所有 Runtime verifier 都返回 PASS。

模型可以提出 PASS，但 Python/Runtime 决定 PASS 是否允许提交。任何一项失败都返回稳定错误，例如：

```text
EVALUATOR_FRESH_INVOCATION_REQUIRED
EVALUATION_INDEPENDENCE_CONTEXT_INVALID
EVIDENCE_PROVENANCE_INSUFFICIENT
EVALUATOR_REPRODUCTION_REQUIRED
EVIDENCE_REVISION_MISMATCH
DETERMINISTIC_PASS_GATE_FAILED
```

## 6. Write Boundary

E1 复用既有 F12 Path Policy 和 Capability Policy，不把写权限搬到 Prompt：

- Evaluator 可以写 evaluation report、issue package、evidence 和受控候选记录；
- Evaluator 不可以写 `code/`、approved plan、requirements、evaluation rules、threshold 或 protected schema；
- `project.yaml` 仍只能由 Runtime CAS 更新；
- 拒绝必须通过正式 Broker/Path Policy，并保留安全审计。

## 7. Recovery

Crash/Resume 的恢复单位仍然是 Durable Session、Checkpoint、Evaluation Transaction、Context Manifest 和 Tool Call State。Fresh Invocation 只改变模型调用边界，不改变项目状态恢复边界。恢复时不得把 Generator 的聊天历史作为 Evaluator Context；如果 Evaluator Invocation 中断，Runtime 结束旧 Invocation，并为重试创建新的 Invocation。

## 8. Host Limitation

```text
Runtime Context Isolation: 可由本 Skill 控制
Host Conversation Isolation: 取决于宿主 ModelInvocationAdapter / Codex API
Independent Evidence Reproduction: 由 E1 verifier 和 Execution/Browser Runtime 控制
Deterministic PASS Gate: 由 E1 Runtime Gate 控制
```

当前不强制不同模型、不要求额外 API Key，也不把 Host Conversation Isolation 未暴露误报为已完成。即使 Generator 与 Evaluator 使用同一模型，只要上述 Runtime 不变量成立，仍能获得基本的验收独立性。

