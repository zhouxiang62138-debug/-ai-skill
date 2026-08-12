# F14 Final Full-System Qualification

报告类型：F14 最终资格验收

报告日期：2026-08-12

当前配置：`f13_full`；Selective Context、Evaluator Selective Context 和 Invocation Gate 全局开关均保持关闭。

## 1. 最终结论

| 项目 | 结论 |
|---|---|
| Engineering Qualification | **CONDITIONAL PASS** |
| Controlled Rollout | **RECOMMENDED，仅限显式 `test_` / Canary，保留 F13 fallback 与 Evaluator Full Context** |
| Global Default Rollout | **NOT RECOMMENDED** |
| Actual Codex / Host Token Saving | **NOT PROVEN** |

原因：修复后的审批来源哈希、Generator fail-closed、既有 Runtime、Fault Injection、Incremental、Escalation、Recovery、Change Request 和受控 A/B 证据均通过；但本轮没有真实 Codex/Host Model usage，也没有完成一条从附件固定的 Case C 用户原话开始、使用真实 Host/真实浏览器的完整 Gold E2E。因此证据足以支持受限 Controlled Rollout 条件，不足以支持 Global Default Rollout 或真实 Token 节省结论。

本轮没有启用全局 F14，没有开启 Evaluator Selective，没有开启全局 Invocation Gate，没有开始 F15 或新的优化阶段。

## 2. 证据等级与 Benchmark 类型

证据严格分层：

| 证据 | Benchmark 类型 | 等级 | 说明 |
|---|---|---:|---|
| 497 项 unittest、F14 focused pytest | `SYNTHETIC` / `CONTROLLED_RUNTIME` | 3–4 | 真实仓库 Runtime/策略路径或隔离夹具；不含真实模型 |
| Stage 2/3/4B + Design Exploration 组合 E2E | `CONTROLLED_RUNTIME` | 2–3 | 真实 Orchestrator、Session Store、CAS、Context、Execution Broker，使用 test-only/controlled adapter |
| A–E 当前 Benchmark Harness | `CONTROLLED_RUNTIME` | 3 | 真实 Benchmark Harness，使用 `f14-final-controlled-adapter`；不是 Real Model |
| Case C Frozen Checkpoint A/B | `CONTROLLED_RUNTIME` | 4 | 同一 Baseline/Inputs 的 F13/F14 受控比较；效率数字来自受控 fixture |
| Real Codex/Host Model | `REAL_MODEL` | 1 | **本轮缺失** |

受控 adapter 的 request count、fixture bytes 和 token 字段没有被写成真实模型 usage。真实 Token 字段保持 `unavailable`。

## 3. Qualification Baseline

测试前冻结的工作区不是干净树，未提交内容属于用户既有变更；没有为了测试自动提交。

```yaml
qualification_baseline:
  git_commit: 0fcb6c98ea421b7a930cd564d15da74a03383b3b
  git_tree_hash: 4039f562e38c2d098612f3b0fd8624999c939e66
  working_tree_hash: ee07f44a11bffeaf3096e36fdd9a56fdf896cb44cab46b15092682803cf1385c
  runtime_schema: 7
  config_hash: 4fe2ff3dd6f4c5d0b9aa89f76f65ad29151107bc5dbb684aeab1f73853684ac6
  workflow_hash: a2484a0ac476b8f34c173850586dfcf665c1afa5119332788b4e9f03a661c8a8
  role_policy_hash: 0de6f66463fee280476c7269811e759ba5a12310beb7702ba53d3035bd23ab45
  context_policy_hash: b172d2683b5c283e85a536af79a5382a213674a793e5f1445d4fcc5c96e11b0b
  evaluation_policy_hash: f6b30b1fe1aab673a58c8e4c9f9c69a5881bb641b8979b931371ed33528240dc
  f14_config_hash: b387a2b5476762b772d076b657ba26e83c8430ffb94274df9393c858d8432ec2
  python_version: Python 3.11.15
  platform: Microsoft Windows NT 10.0.26200.0 / AMD64
```

修复后工作树指纹为 `be79a3da5a0191c3727c8c619eab1bfd5c7c39c4b99ae76e383c094d80d04be2`；最终配置哈希为 `196d6c73c3f55420167587e4d2289b0406d01541572f641bdf2134d9c3c30bc1`，最终 Role Policy 哈希为 `9d7951d816f5a41a83cd0d167b38e27766c26aabe8a88cb76c573156dcd94fe2`。

## 4. 回归与组合 E2E

| 验证 | 结果 | 证据 |
|---|---:|---|
| F14 focused pytest | 116 passed，16 subtests passed | `tests/test_f14_*.py` |
| 原重点回归 | 120/120 PASS | `test_context_*`、`test_formal_context_builder`、`test_role_thread_isolation`、`test_evaluator_independence`、`test_evaluation_*` |
| Full unittest discovery | **497/497 PASS** | `python -m unittest discover -s tests -p 'test*.py'` |
| Stage 2/3/4B + Design Exploration | **39/39 PASS** | `tests/test_stage2_core_e2e.py`、`test_stage3_rework_e2e.py`、`test_stage4b_change_request_e2e.py`、`test_two_stage_design_exploration.py` |
| `git diff --check` | PASS | 当前工作树 |
| Full pytest 全量 | 未完成 | 184 秒工具时限内无输出超时，不作为 PASS 或 FAIL |

首次 120 项执行曾因沙箱禁止创建仓库外临时目录产生 29 个环境错误；换用仓库外 `test_` 目录后通过。Full pytest 全量同样只归类为 `ENVIRONMENT FAILURE`，不改变已复现的 497/497 unittest 与 F14 focused 结果。

## 5. 主动证伪与真实缺陷

### DEF-F14-001：批准来源只有路径引用，没有内容哈希绑定

- 根因：`validate_generator_gate` 原来只验证状态、路径、章节和来源字符串；修改已批准 Plan 内容后仍可返回空错误。
- 严重性：P1，影响 Approval Integrity 与 Generator Source Chain。
- 修复：产品批准和 Plan 批准时追加记录受保护来源的 SHA-256；Generator Gate 逐项复核 `reference`、当前内容哈希和 `approved_plan_hash`。哈希以安全列表记录，避免文件路径成为 YAML 映射键。
- 复验：Plan、Product Spec、Approval Record 等未授权修改均返回结构化拒绝；7/7 source-chain fault clone fail closed。

### DEF-F14-002：`approved_plan` 缺失时 Gate 抛出 TypeError

- 根因：缺失字段在进入 `_read_artifact` 前没有确定性预检。
- 严重性：P1，影响 fail-closed 与恢复边界。
- 修复：Generator Gate 在读取文件前检查全部受保护来源字段，缺失时返回结构化错误。
- 复验：缺失 Plan、缺失产品批准、缺失原型确认、需求 revision、Plan supersede 等 7 项 fault 全部 fail closed。

两个缺陷均已修复，并重跑 120、497、F14 focused 与组合 E2E；没有未解决的 P0/P1。

## 6. Case Matrix

| Case | 覆盖目标 | 当前证据 | Verdict |
|---|---|---|---|
| A Simple/Simple | 低业务/技术复杂度，不无意义过度执行 | A–E controlled harness；默认路线为 requirements → planning → implementation → evaluation | CONTROLLED PASS |
| B Simple Product/Showcase | 低产品复杂度不能压低设计深度、动效和 Evaluation | 两阶段 Design Exploration 组合测试；exactly 3 方向和独立 Gate 通过 | CONTROLLED PASS；真实浏览器视觉质量未证明 |
| C Gold E2E | 个人记账 App 完整生命周期 | Stage 2/3/4B、requirements discovery、design、change request 组合证据；Case C A/B 有 Frozen Checkpoint 受控证据 | CONDITIONAL；未完成固定用户原话起点的真实 Host Gold E2E |
| D Complex Business | 多角色、权限、导入、报表、集成、Change Request | Change Request E2E、权限/安全/回归测试、F14-D/E 受控测试 | CONTROLLED PASS |
| E Low Complexity/High Risk | 功能少不能降低 Research、约束、证据和 Evaluation rigor | requirements discovery、Research unavailable、Contract/Verifier、F14 安全测试 | CONTROLLED PASS；真实高风险生产用途不在范围 |

## 7. Requirements、Research、Design 与 Approval

| 主题 | 结果 |
|---|---|
| 模糊需求不能直达 Plan | PASS：Intent → Research Gate → Coverage → Gap → Questions → Sufficiency 有测试覆盖 |
| 每轮 1–3 个问题 | PASS：问题按轮生成，不把总数限制为 1–3 |
| `answered/assumed/undecided/...` | PASS：未知不会被转成虚假确定事实 |
| Research 必要性 | PASS：required/optional/not_required 可区分；受监管场景要求 Research |
| Research 越权 | PASS：Research 输出保留 FACT/PATTERN/OBSERVATION/INFERENCE/OPPORTUNITY/IDEA 和 trust boundary，不自动成为 Must Have |
| Research Adapter 不可用 | PASS：返回 `unavailable`，不伪造结果；本轮没有接入真实研究 Host |
| Design Exploration | PASS：Case B/C 的 controlled tests 验证三方向结构差异、selected prototype 单独轮次和浏览器工件规则 |
| “三个都不喜欢” | PASS：开启新 round |
| “1 的结构 + 3 的视觉” | PASS：记录 Blend，不覆盖旧历史 |
| “看起来不错” | PASS：不能跨 Prototype Confirmation、Product Approval、Plan Approval |
| 四个独立产品/设计 Gate | PASS：方向选择、原型确认、产品批准、Plan 批准分别验证 |
| Change Request Approval | PASS：Stage 4B 验证 ACCEPTED → CHANGE_REQUESTED → Impact → Approval → Generator → Regression → RELEASE_READY → ACCEPTED |

## 8. Generator Source Chain 与 Evaluator Independence

### Generator

修复后 7 项 fault clone 结果：

| Fault | 结果 |
|---|---|
| approved plan missing | DENY / fail closed |
| plan hash mismatch | DENY / fail closed |
| product approval missing | DENY / fail closed |
| prototype confirmation missing | DENY / fail closed |
| requirements revision mismatch | DENY / fail closed |
| approved plan superseded | DENY / fail closed |
| unauthorized artifact modification | DENY / fail closed |

### Evaluator

Evaluator 组合测试要求 fresh invocation、independent context、current revision、runtime evidence、own verification，并保留 broader regression boundary、Mandatory Regression、Current Code Snapshot 和 Independent Evidence。Evaluator 不继承 Generator 的 coverage、reasoning、self-evaluation 或 changed-files-only scope。相关测试通过；本轮没有真实 Model，因此独立性结论是 `CONTROLLED_RUNTIME`，不是 Level 1。

## 9. F14-D/E/F/G 与安全边界

- F14-D：Indexed ID、revision、role、path、authority、secret scan、L1/L2/L3、预算、循环、unknown dependency 和 F13 recovery 均有受控测试；非法请求 DENY、FALLBACK 或 BLOCK，不继续猜测。
- F14-E：no change、单文件变化、依赖传播、全局约束、policy/parser/summary/cache 变化、unknown dependency 和 coverage 不确定均通过增量/Full Safe 等价或 fallback 测试。
- F14-F：Invocation Gate 对 hash/schema/revision/CAS/approval/hash/cache 等确定性任务走 Python-only；product decision、ambiguity、bug diagnosis、architecture、UX、implementation、evaluation judgment 等语义任务保留 LLM_REQUIRED；已测试集合中的 Semantic Task Misclassified Python-only 为 0。
- F14-G：Telemetry 分开记录 Runtime、Context、Model、Quality、Escalation、Incremental 和重复 Context；不保存 Secret、private reasoning 或 raw model output。
- Fallback：Selective Builder、Index、Graph、Cache、Incremental 或 Expansion 失败时保留正式 F13 Context；当前正式配置仍是 F13 Full。

## 10. Frozen Checkpoint A/B 与效率证据

Case C 使用同一 Baseline、requirements、Product Spec、approved Plan、code/revision-equivalent snapshot、tests、environment 和 controlled model fixture，仅比较 F13 Full 与 F14 Selective。

```yaml
ab:
  test_id: QUAL-EFF-001
  benchmark_type: CONTROLLED_RUNTIME
  baseline_id: F14-G-BASELINE-003
  baseline_freeze_hash: b818a1996f4df751405e0913f29b26f4b8b3c01753d01d4ec687e037617eadfa
  f13_full_bytes: 10000
  initial_selective_bytes: 5200
  expansion_bytes: 2200
  recovery_bytes: 400
  net_context_bytes: 7800
  net_context_reduction_bytes: 2200
  net_context_reduction_ratio: 0.22
  f13_repeated_context_bytes: 6000
  f14_repeated_context_bytes: 3500
  repeated_context_reduction_bytes: 2500
  f13_real_model_requests: 0
  f14_real_model_requests: 0
  critical_detection_difference: 0
  token_usage_status: unavailable
```

报告使用的是净成本：`5200 + 2200 + 400 = 7800`。没有把初始 48% gross reduction 冒充 22% net reduction，也没有把 fixture bytes 转换成 Token。

A–E 当前 controlled harness 的质量 Gate 均通过；每个 Case 的 `mandatory_covered=4/4`、`required_ac_covered=3/3`，Critical False Omission、Approval、Regression、Browser Gate、Security、Privacy 和 Evaluator Independence failure 均为 0。A–E 受控结果归档在仓库外 `test_f14_final_qualification_20260812b/archive/benchmark-results/`，不进入 Skill 仓库。

## 11. Final Qualification Matrix

| Domain | F13 Baseline | F14 Optimized | Parity/Delta | Verdict |
|---|---:|---:|---:|---|
| Requirement Coverage | 4/4 controlled | 4/4 controlled | 0 omission | PASS（controlled） |
| Approval Integrity | 既有 Gate + hash 修复后 | hash-bound fail closed | 7/7 faults blocked | PASS（controlled） |
| Generator Correctness | Stage E2E | Stage E2E + rework | 39/39 E2E | PASS（controlled） |
| Evaluator Detection | Full independent boundary | same boundary | critical difference 0 | PASS（controlled） |
| Security | F12/F14 tests | F13 fallback on failure | 0 security miss in tested set | PASS（controlled） |
| Privacy | Research/secret/cache tests | same constraints | 0 privacy miss in tested set | PASS（controlled） |
| Regression | 497/497 | 497/497 | 0 existing regression | PASS |
| Browser QA | Harness/static browser evidence | No real browser Gold run | not proven | GAP |
| Crash Recovery | recovery/idempotency tests | same Runtime path | no corruption observed | PASS（controlled） |
| Change Request | Stage 4B | Change + regression | 39/39 combo E2E | PASS（controlled） |
| Formal Context Bytes | 10000 fixture bytes | 10000 formal / 7800 net | 2200 net reduction | PASS（controlled） |
| Net Context Bytes | 10000 | 7800 | -22% | PASS（controlled） |
| Repeated Context Bytes | 6000 | 3500 | -2500 | PASS（controlled） |
| Escalation Cost | F13 baseline path | F14-D controlled path | full production rate not measured | GAP |
| F13 Fallback | safety baseline | default and failure recovery | preserved | PASS |
| Python-only Work | deterministic-only | semantic tasks remain LLM_REQUIRED | 0 tested misclassification | PASS（tested set） |
| Real Model Requests | 0 in this run | 0 in this run | no A/B real model sample | NOT PROVEN |
| Actual Tokens | unavailable | unavailable | no conversion from bytes | NOT PROVEN |

## 12. Efficiency Attribution

本轮能被当前证据支持的收益：

- Context bytes reduction：Case C controlled net `2200 bytes / 22%`。
- Repeated Context reduction：Case C controlled `2500 bytes`。
- Runtime work reduction：F14-E incremental reuse、cache/parser/index/diff telemetry 有受控证据。
- Model request reduction：**未证明**；Case C A/B 两侧 real model requests 都是 0，不能从 controlled fake request 推导真实 Host reduction。
- Actual Token reduction：**未证明**；所有 Token 字段为 `unavailable`。

最值得优化的 Role 是 Generator Rework，其次是 Change Request Generator 与 Planner Revision；最值得优化的 Phase 是 Generator Rework、Change Request 和重复的 Evaluation/Regression Context。此判断来自 controlled telemetry 和既有 F14-G 结构，不是 Real Model usage 结论。

Fallback 是否抵消收益：本轮未有生产频率样本，不能量化；安全底座仍为 F13 Full，任何不确定性优先 fallback/block。

## 13. 对 26 个核心问题的回答

1. 用户需求有没有漏：受控 Requirements/Approval/E2E 集合没有发现遗漏；固定 Case C 的完整真实 Host 生命周期仍是证据缺口。
2. Research 有没有越权：没有；输出有信任边界，不自动升级为 Must Have。
3. Design Exploration 有没有差异：有，受控验证为三方向和结构差异；真实视觉完成度未证明。
4. 四个 Gate 有没有绕过：受控验证没有；模糊肯定不会跨 Gate。
5. Generator 是否只能执行 approved source chain：修复后是；7/7 fault clone fail closed。
6. Evaluator 是否独立：受控 Runtime 中是；真实模型边界未执行。
7. Baseline/Optimized Evaluator 检测能力是否一致：Case C controlled A/B critical detection difference 为 0。
8. Rework 是否可靠：Stage 3 与 F14-E/F 受控通过；真实模型 rework 未验证。
9. Max 5 是否可靠：既有 retry governance/full unittest 通过；本轮没有真实模型连续五次失败样本。
10. Change Request 是否可靠：Stage 4B 组合 E2E 通过，原功能回归保留。
11. Crash 后是否可恢复：Session/Event/Checkpoint/Revision/Lease 受控测试通过；没有真实 Host crash 样本。
12. Context corruption 是否 fail safe：Cache/Manifest/Graph/Expansion corruption 测试通过，fallback/block。
13. Selective Context 是否漏 Mandatory：受控 C/D/E 集合为 0 false omission；生产全量覆盖未证明。
14. Escalation 能否补回缺失：F14-D L1/L2/L3 controlled tests 通过；失败时回 F13。
15. Incremental 与 Full 是否等价：Mandatory、Authority、Hash、Locator、Role、Revision、Coverage 受控等价测试通过。
16. Invocation Gate 是否误杀语义任务：测试集合中 Semantic Task Misclassified Python-only 为 0；仍需 Real Model Canary。
17. F14 净减少多少 Context：Case C controlled net `2200 bytes`，`22%`。
18. 重复 Context 减少多少：Case C controlled `2500 bytes`。
19. 哪个 Role 最值得优化：Generator Rework，其次 Planner Revision/Change Request Generator。
20. 哪个 Phase 最值得优化：Rework、Change Request、Evaluation/Regression 的重复 Context。
21. Fallback 是否频繁抵消收益：没有生产频率证据，未证明。
22. Real Model Request 是否真的减少：未证明；本轮 Real Model requests 为 0。
23. Actual Host Token 是否取得：没有，`token_usage.status=unavailable`。
24. 哪些节省已证实：Context bytes、重复 bytes、受控 Runtime reuse；哪些未证实：Real Model requests、Actual Host Tokens、真实生产 fallback rate。
25. 是否可以 Controlled Rollout：可以，但只限显式 `test_` / Canary、F13 fallback、Evaluator Full Context、保留独立验证。
26. 是否可以 Global Default Rollout：不可以；等待真实模型/真实浏览器 Gold E2E、生产 Runtime 频率和更完整 Case C 证据。

## 14. Failure Classification

| 分类 | 事件 | 结论 |
|---|---|---|
| PRODUCT DEFECT | DEF-F14-001、DEF-F14-002 | 已修复并重跑相关 Qualification |
| TEST DEFECT | A–E 回调签名错误、首次 fault matrix 恢复文件过早 | 已纠正测试后重跑，不计入产品失败 |
| ENVIRONMENT FAILURE | 沙箱拒绝仓库外 tempfile、全量 pytest 工具超时 | 不计入产品失败；保持证据缺口记录 |
| CONTROLLED ADAPTER LIMITATION | controlled adapter 无真实 usage/语义随机性 | 不冒充 Real Model，结论降级为 Conditional |
| REAL MODEL VARIANCE | 无样本 | 未发生，不能推断 |
| EXPECTED SECURITY BLOCK | 非法路径、secret、stale、unknown、越权请求 | 预期 DENY/FALLBACK/BLOCK，计为安全通过 |
| EXPECTED COVERAGE BLOCK | Mandatory/authority/revision 不完整 | 预期阻断，计为安全通过 |
| UNKNOWN | 0 个未归因产品结果 | — |

## 15. 停止边界

本报告完成后停止。不会自动修改 `config/f14.yaml` 的默认 `f13_full`，不会全局启用 Selective Context、Evaluator Selective 或 Invocation Gate，不开始 F15，不创建新的优化阶段。所有剩余证据缺口交由用户评审。
