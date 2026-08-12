# F14-C Context Architecture Report

报告日期：2026-08-11  
实施模式：Shadow-First Context Architecture  
最终结论：**PASS**

## 1. 范围与停止边界

本阶段完成 C0–C6：

- C0：Control Plane、Authority、真实模型调用边界 Preflight。
- C1：Context Semantic Model。
- C2：Shadow Classification。
- C3：Shadow Coverage Gate。
- C4：Enforced Coverage Gate。
- C5：Selective Context Candidate Evaluation。
- C6：Fault Injection、回归测试和本报告。

本阶段没有启动 F14-D，没有开放模型任意路径读取，也没有把候选 Context 设为默认正式输入。

## 2. 不变量审计

以下边界保持不变：

- Planner、Generator、Evaluator 三个角色不变，没有创建第四个 Agent。
- F13 当前正式 Context 内容和默认交付路径不变。
- Planner / Generator / Evaluator 的正式 Invocation 数量没有因为 F14-C 被压缩、复用或跳过。
- E1 Evaluator Independence、Role Isolation、Approval、Lease、Revision、CAS 没有被绕过。
- `project.yaml` 没有被 Context 优化器或派生索引写入。
- 失败时不会把 Context 变成空值；优化器故障回退到现有 F13 Full Safe Context。

## 3. C0 Preflight Gate

### C0-1：Control Plane Storage Boundary

PASS。正式 Runtime 配置仍使用：

```text
~/.ai-development-team/runtime/<control_plane_id>/sessions.sqlite3
```

`Orchestrator` 通过 `require_session_database()` 打开外部 Session Store，F14-B 和 F14-C 派生表都附着在调用方提供的 F10 `SessionStore` 上。没有创建或使用 `<project>/.runtime/sessions.sqlite3` 作为正式 Control Plane。

单元测试中的临时 SQLite 是隔离测试夹具，不是产品 Runtime 路径；C0 专项测试单独验证了生产路径位于项目工作区之外。

### C0-2：Authority Must Be Runtime-Derived

PASS。新增 `RuntimeAuthorityVerifier`，调用方传入的 `authority` 只被视为待验证声明，不再直接成为可信事实。

- `APPROVED_REQUIREMENT` 必须绑定当前 `active_requirements`、需求状态、revision 和内容 hash。
- `APPROVED_PRODUCT_SPEC` 必须绑定当前 `active_product_spec` 和 finalized 状态。
- `APPROVED_PLAN` 必须绑定当前 `approved_plan`、Plan approval 状态，并通过既有完整 Generator source-chain gate。
- `APPROVED_CHANGE_SCOPE` 必须绑定当前 `change_approval_record`、active Change Request 和批准的 Change Items。
- `USER_EXPLICIT`、`RUNTIME_EVIDENCE`、`GENERATED_ARTIFACT`、`HISTORICAL` 和 `UNKNOWN` 分别要求明确 proof/status，不允许用模型推测替代证据。

伪造 locator、伪造 revision、残缺 `project.yaml` 声称 `APPROVED_PLAN` 的负向测试均被拒绝。

### C0-3：Real LLM Invocation Telemetry

PASS，且修正了边界语义：

- Fresh Invocation 在 `RoleExecutionBroker` 即将进入 Model Adapter 前触发观测，因此 Adapter 抛错也会被记录为真实请求尝试。
- Child Thread 只记录 `role_thread_dispatch_boundary`，不冒充实际模型服务请求；真实模型请求计数不会因 Host Thread dispatch 自动增加。
- python-only、perception、reused deterministic、rollover 等生命周期类型仍与 `real_llm_invocations` 分离。

## 4. C1：Context Semantic Model

新增旁路数据模型，不改写 F13 Package：

```yaml
task_identity:
role:
project_revision:
dependency_roots:
  requirements:
  acceptance_criteria:
  issues:
  plan_tasks:
  changed_files:
  change_request_items:
constraints:
  global_constraints:
  task_constraints:
  approval_constraints:
  safety_constraints:
context_classes:
  mandatory:
  task_relevant:
  on_demand:
  omitted:
  unknown:
```

每个 `ContextUnit` 均携带 `authority`、`source_ref`、`source_hash`、`exact_locator`、`project_revision`、`role_scope`、`reason`、`dependency_path` 和 `delivery_mode`。`ContextBuilder.build_semantic_model()` 通过旁路 API 生成并追加保存该模型，默认 F13 `ContextPackage` 的 hash、sources 和 delivery 不变。

## 5. C2：Shadow Classification

`ShadowClassifier` 以 `Role + Task + Revision` 为作用域，计算：

- 当前来源的分类。
- Global / Approval / Safety Constraint 的 mandatory closure。
- 显式 Dependency Graph 的 deterministic closure。
- 与 mandatory closure 相连的 unknown edge 的影响节点。
- stale、conflict、unknown 和 missing 节点。

Unknown edge 不会被降级为 `NO_IMPACT`。当 unknown edge 与 mandatory closure 相连时，相关端点会进入待处理影响集合。

## 6. C3：Shadow Coverage Gate

Coverage 状态为：

```text
COVERED
MISSING
STALE
CONFLICT
UNKNOWN
NOT_APPLICABLE
```

只有 `COVERED` 或有权威证据的 `NOT_APPLICABLE` 才能通过。缺失 AC、过期来源、hash 冲突、未知依赖、未验证 authority 和缺失 Security / Privacy Constraint 都会导致 Shadow Gate FAIL。

Shadow Comparison 同时生成：

- current / candidate Context bytes。
- potential reduction bytes / ratio。
- mandatory total / covered / missing / stale / conflict / unknown。
- task relevant、on demand、omitted、fallback 计数。
- current 与 candidate 的精确 source ID 集合。

## 7. C4：Enforced Coverage Gate

`EnforcedCoverageGate` 位于模型调用前：

- mandatory coverage 不完整：`BLOCK`，模型调用不启动。
- authority、revision 或依赖冲突：`BLOCK`，模型调用不启动。
- Shadow / Index / Candidate 优化器崩溃：`FALLBACK_F13`，使用现有完整 F13 Context，不传空 Context。
- Coverage 通过：仍使用 `F13_FULL`，本阶段只验证覆盖，不执行 Context 裁剪。

## 8. C5：Selective Context Candidate Evaluation

`SelectiveContextEvaluator` 只生成候选评估工件，计算：

- `current_context_bytes`
- `candidate_context_bytes`
- `reduction_ratio`
- `mandatory_coverage`
- `unknown_count`
- `authority_verified`
- `fallback_to_f13`

只有 mandatory coverage 完整、unknown 为零、authority 全部验证且没有 stale/conflict 时，候选才会被标记为 eligible。eligible 不等于默认启用，也不触发正式 Model Context 替换。

## 9. F10 派生存储

Runtime schema 以追加方式升级到 v11，新增派生表：

- `f14_context_semantic_snapshots`
- `f14_shadow_comparisons`
- `f14_shadow_gate_results`
- `f14_selective_candidates`

所有表都有 append-only trigger、幂等 ID 和 integrity hash。Context 架构工件不进入项目业务工作区，不修改业务状态投影。

## 10. C6 Fault Injection 与回归证据

| 验证集合 | 结果 |
|---|---:|
| F14-C focused | 21/21 PASS |
| F14-B focused | 30/30 PASS |
| 原 F14-A 重点回归 | 120/120 PASS |
| Full `unittest discover` | 451/451 PASS |
| F14-C6 fault injection | 7/7 PASS |
| C0–C6 + F14-B + RoleExecution targeted | 67/67 PASS |

Full baseline 命令：

```text
python -m unittest discover -s tests -p 'test*.py'
```

最终运行结果：`Ran 451 tests in 43.071s — OK`。

已覆盖的关键故障包括：

- Global Security / Privacy Constraint 无直接 Graph Edge。
- Mandatory Requirement / AC 缺失。
- Approved Scope 或 Plan revision 过期。
- Requirement / Plan hash 冲突。
- Dependency Edge unknown。
- Artifact / Index / Shadow 优化器损坏或不可用。
- Evaluator 不能继承 Generator 的跨角色 coverage。
- Candidate 遗漏关键用户禁止事项。
- Model Adapter 在真实请求边界抛错。
- F13 fallback 不得变成空 Context。

## 11. Context Efficiency 证据边界

受控候选 fixture 中，current Context 为 800 bytes、candidate 为 100 bytes，潜在减少 700 bytes、潜在 reduction ratio 为 87.5%。这是 Shadow Candidate 的潜在值，不是正式 Model Context 已减少的声明。

本阶段正式 Context 仍为 F13 Full Context；没有生产 workload 上的 token reduction 结论。当前 Host 未提供可可靠读取的 input / cached input / output token usage，因此模型 token 字段保持 `unavailable`。没有宣称正式 LLM Invocation 数量减少。

## 12. 最终二十项回答

1. **C0 Control Plane Boundary 是否正确？** 是，正式入口使用项目外 F10 Runtime Store。
2. **Artifact Authority 是否 Runtime 验证？** 是，校验批准字段、精确 locator、revision、source hash 和来源链。
3. **Real LLM Invocation Telemetry 是否准确？** Fresh Adapter 边界准确；Child Thread 只记 dispatch，不冒充真实模型请求。
4. **Mandatory Context 如何计算？** 按 Role、Task、Revision 绑定，并合并依赖、Global Constraint、Approval Closure。
5. **Global Constraint 如何进入 Context？** 独立 constraint 集合自动加入 mandatory closure，不依赖直接 Graph Edge。
6. **Task Dependency Closure 如何构建？** 沿 explicit edge 做确定性闭包；unknown edge 显式进入 unknown impact。
7. **Approval Closure 如何构建？** 由 Runtime authority resolver 绑定 project state、批准记录、revision 和 hash。
8. **Coverage 状态是什么？** `COVERED / MISSING / STALE / CONFLICT / UNKNOWN / NOT_APPLICABLE`。
9. **Shadow 发现了多少遗漏？** 受控故障注入覆盖缺失、过期、冲突、unknown 和全局约束遗漏；没有把这些错误判为 PASS。
10. **False Omission Rate？** 在本阶段受控故障样例中为 0 次误放行；尚未对真实生产项目语料做统计估计。
11. **False Mandatory Rate？** 受控 fixture 中没有观察到错误 PASS；生产语料精度仍需后续独立评估。
12. **Critical Mandatory Miss？** Security / Privacy / Approved Scope 漏载会被检测并 FAIL，未观察到关键遗漏被放行。
13. **Evaluator 是否保持独立 Coverage？** 是，保持 fresh invocation、independent context、current revision 和 independent evidence。
14. **Fallback 到 F13 是否可靠？** 是，优化器崩溃和索引不可用均回退现有 F13 Full Safe Context。
15. **Current Context 平均大小？** 本阶段未宣称生产平均值；受控 fixture 为 800 bytes。
16. **Candidate Context 平均大小？** 本阶段未宣称生产平均值；受控 fixture 为 100 bytes。
17. **潜在 Context Reduction？** 受控 fixture 为 700 bytes / 87.5%，仅作候选评估证据。
18. **Model Invocation 是否减少？** 没有正式减少，也没有跨角色结果复用。
19. **正式 Model Context 是否改变？** 没有，C4 仍交付 F13 Full Context。
20. **F14-C 最终结论？** **PASS**，Shadow-First C0–C6 全部完成并通过证据测试。

## 13. 停止条件

F14-C 报告完成后停止。当前没有自动进入 F14-D，也没有开放 Model Arbitrary Context Request。
