# E1 Evaluator Independence Hardening：E0 审计报告

## 审计范围

本审计检查当前 Skill 本体中的正式调用路径：

- `runtime/phase_runner.py`
- `runtime/orchestrator.py`
- `runtime/session_store.py`
- `runtime/context/`
- `runtime/verifiers.py`
- `runtime/execution/`
- `config/context.yaml`
- `config/evaluation_protocol.yaml`
- `config/evaluation_gates.yaml`
- `config/role_policies.yaml`
- `scripts/evaluation_evidence.py`
- `tests/`

本次升级命名为 **E1 Evaluator Independence Hardening**。它与 F11 Execution、F12 Security、F13 Context 并列，属于“验收可信度”升级；F11/F12/F13 是 E1 的依赖能力，但不是 E1 的归属阶段。

## CURRENT STATUS

**PARTIALLY_INDEPENDENT**

当前系统已经不是纯 Prompt 角色扮演，但还不能把 Evaluator 的最终判定称为完整独立验收。Runtime 层已经有独立 Invocation、角色级 Context Builder、Runtime Verifier 和受保护路径策略；不过 Evaluator 仍能收到过宽的 Generator 交接来源，且最终 Gate 尚未把 Fresh Invocation、证据 provenance、当前 revision/code snapshot 和独立重现绑定成一个不可绕过的确定性门禁。

## 发现 1：Invocation Boundary

### 当前事实

1. `Orchestrator.run_phase()` 通过 `PhaseRunner` 进入正式 Role Run。
2. `PhaseRunner.run()` 每次先由 `ContextBuilder.build()` 创建 Context，再调用 `SessionStore.create_model_invocation()`。
3. `model_invocations` 表与 Durable Session 分开保存，并有独立的 `invocation_id`、序号、状态和完成事件。
4. Planner、Generator、Evaluator 的 Runtime Role Run 使用不同的调用记录；返工也会创建新的 Role Run。
5. `ModelInvocationAdapter` 只接受结构化 `ModelInvocationRequest`，但当前 Request 没有显式的 source revision、model profile、Fresh Context 要求或 Host Conversation Isolation 能力声明。
6. Runtime 能控制“传给 Adapter 的结构化输入”，但不能证明宿主 Codex 对话窗口不会把外层自由聊天历史隐式注入 Adapter。当前只能确认 Runtime Context Isolation，不能确认 Host Conversation Isolation。

### 判断

- Durable Session 与 Model Invocation 已经区分：**PASS**。
- Generator → Evaluator 的内部 Invocation ID 已经可不同：**基本 PASS**。
- Evaluator 必须 Fresh Invocation、且不可继承上一 Invocation 历史：**未形成确定性 Gate**。
- 宿主对话历史隔离：**UNAVAILABLE / 未暴露能力**。

## 发现 2：Evaluator Context

### 当前 Evaluator 可见来源

由 `config/context.yaml` 的 evaluator 规则与 `ContextBuilder` 汇总，当前包括：

- `project.yaml`
- `approved_plan`
- `active_product_spec`
- 批准 Reference Contract 摘要
- `last_generator_response`
- `latest_handoff`
- `last_evaluation`
- `last_issue_package`
- `evidence_manifest`
- Change Request 记录、批准记录和基线
- `current_release`
- `evaluation_profile`

### 风险

`last_generator_response` 是 Generator 交接/自我叙事的宽泛入口。当前没有一个独立的 Context Manifest 字段声明哪些来源是“仅供定位”、哪些来源是“可作为证据”，也没有在 Runtime 层把 Generator 的结论性语言转换成 `CLAIM → VERIFY → RESULT`。

### 判断

- Context Builder 由 Runtime 正式控制：**PASS**。
- 最小必要 Context：**PARTIAL**。
- Generator 完整聊天历史和推理不应进入 Evaluator：**没有显式阻断规则**。
- Generator handoff 不自动成为 PASS Evidence：**Prompt 有说明，Runtime Gate 未完整执行**。

## 发现 3：Evidence Independence

### 当前事实

- F9 已有 Evidence Manifest、命令记录、Browser Evidence、受保护快照和 Gate 顺序。
- `RuntimeVerifierRegistry` 会要求 Evaluator 返回 `evidence_references`，并对 Reference Conformance 做确定性校验。
- `scripts/evaluation_evidence.py` 可以重新执行白名单命令并记录 `executed_by: EVALUATOR`。

### 缺口

- Evidence schema 没有统一强制 `GENERATOR_PROVIDED`、`EVALUATOR_REPRODUCED`、`RUNTIME_VERIFIED`、`EXTERNAL` provenance。
- Required/Critical Acceptance Criterion 没有确定性要求至少包含 Evaluator 重现或 Runtime 验证来源。
- 当前 verifier 主要检查“存在 evidence reference”，没有统一校验 `tool_call_id`、`attempt_id`、`project_revision`、`code_snapshot_hash` 和结果摘要之间的绑定。
- Generator 自己的测试结果仍可作为交接输入；Runtime 还没有把它们和最终验收证据做硬隔离。

### 判断

**CONTEXT_ISOLATED_BUT_EVIDENCE_SHARED**，并且 Evidence provenance 仍需补强。

## 发现 4：PASS Gate

当前正式链路具备：

- Required steps 不可由调用方缩小；
- Evaluator 必须使用 Runtime Verifier Registry；
- F9 必需 Gate、证据完整性、开放 blocker/critical 和受保护工件检查；
- Phase Attestation、Lease、CAS 和 project revision 校验。

但 `evidence_manifest`、`issue_package`、`candidate` 和 `evaluation_transaction` 等步骤仍主要依据模型响应中的 evidence references。当前没有一个统一的 E1 Deterministic PASS Gate 同时拒绝以下情况：

- Evaluator Invocation 不是新的 Invocation；
- Context Manifest 缺少独立验收标识；
- required evidence 只有 Generator 声明；
- evidence 来自旧 project revision 或旧 code snapshot；
- required tests/browser/regression 没有由 Evaluator/Runtime 重现；
- 模型返回 PASS 但独立 Gate 失败。

### 判断

当前 PASS 是“模型结构化响应 + 既有 Runtime Verifier”，还不是完整的“模型建议 + E1 Deterministic PASS Gate”。

## 发现 5：Write Boundary

当前 `config/role_policies.yaml` 与 `runtime/execution/path_policy.py` 已经提供实际路径边界：

- Evaluator 禁止写 `code/`；
- Evaluator 禁止写 `config/evaluation_rules/`；
- Evaluator 禁止直接写 `project.yaml`；
- Evaluator 只允许写 evaluation 工件、受控 handoff/state 字段和 Release/Change Request 相关目录；
- Capability Policy 还不给 Evaluator `filesystem.write`，形成额外拒绝层。

### 判断

Evaluator 的业务代码/计划/评估规则写权限边界：**基本 PASS**。但当前缺少专门的 E1 自动化测试，证明尝试修改每一类受保护目标都会在正式 Broker/Path Policy 层被拒绝并留下安全审计。

## 正式调用路径结论

```text
User Conversation
  -> Durable Session
  -> Orchestrator.start / run_phase
  -> Role Run
  -> ContextBuilder.build
  -> SessionStore.create_model_invocation
  -> ModelInvocationAdapter.invoke
  -> RuntimeVerifierRegistry
  -> Phase Attestation
  -> Runtime CAS
```

这条路径已存在，但 E1 需要把 Evaluator 的独立性从“存在独立记录”升级为“Invocation、Context、Evidence、Reproduction、Write Boundary 和 Deterministic PASS Gate 的联合不变量”。

## E0 结论

E1 不应被塞入 F11、F12 或 F13：

- F11 提供执行与快照能力；
- F12 提供 Capability、Credential、Network 与 Path Boundary；
- F13 提供 Context Builder、Budget 与 Resume；
- **E1 负责把这些能力组合成可信的独立验收闭环**。

下一阶段应先建立 E1 Architecture Design，再实施 Fresh Evaluator Invocation、最小必要 Context、Evidence Provenance、Independent Reproduction 和 Deterministic PASS Gate。

