# F14-R — Real-Model Canary & Rollout Qualification

报告日期：2026-08-12  
Qualification 类型：`F14_R_REAL_MODEL_CANARY`  
最终结论：**BLOCKED — INSUFFICIENT REAL-MODEL EVIDENCE**

本轮的目标是验证真实 Codex / Host Model 与真实 Browser，而不是把受控 Fixture 重新包装成真实证据。当前正式配置继续保持 `f13_full`，没有开启全局 Selective Context、Evaluator Selective Context 或 Invocation Gate，也没有启动 F15。

## 1. Executive Verdict

| Gate | 结论 |
|---|---|
| F14-R Engineering Qualification | **BLOCKED — INSUFFICIENT REAL-MODEL EVIDENCE** |
| Controlled Runtime Quality | **PASS（受控范围）** |
| Real Model Efficiency | **BLOCKED** |
| Real Browser Gold E2E | **BLOCKED** |
| Global Default Rollout | **NOT RECOMMENDED** |
| Actual Token Saving | **NOT PROVEN** |

当前可复现的受控证据没有发现新的 P0/P1 产品缺陷：497/497 unittest、116 项 F14 focused pytest、10 项重点生命周期/Design/Change Request E2E 均通过。它们不能替代真实模型调用、真实 Token usage 或真实 Browser QA，因此不能把 F14 从 `f13_full` 升级为 Global Default。

## 2. Baseline

本轮基线在任何 Canary 前冻结。工作树延续上轮用户变更，状态为 `WORKING TREE NOT CLEAN`；没有执行 commit、reset、stash 或删除。

| 字段 | 值 |
|---|---|
| `git_commit` | `0fcb6c98ea421b7a930cd564d15da74a03383b3b` |
| `git_tree_hash` | `4039f562e38c2d098612f3b0fd8624999c939e66` |
| `working_tree_hash` | `7b8d2edcbdeab54e7c4e8eedb7d745e91ff2404c0115cbd538879bb6642c6ce6` |
| `runtime_schema` | `7` |
| `config_hash` | `b172d2683b5c283e85a536af79a5382a213674a793e5f1445d4fcc5c96e11b0b`（`config/context.yaml`） |
| `workflow_hash` | `a2484a0ac476b8f34c173850586dfcf665c1afa5119332788b4e9f03a661c8a8` |
| `role_policy_hash` | `9d7951d816f5a41a83cd0d167b38e27766c26aabe8a88cb76c573156dcd94fe2` |
| `evaluation_policy_hash` | `f479212320476db725e25cb0ba93ba9b7255bf1ac7290cfd6fcf0436845c66f5`（`config/evaluation_gates.yaml`） |
| `f14_config_hash` | `b387a2b5476762b772d076b657ba26e83c8430ffb94274df9393c858d8432ec2` |
| `qualification_timestamp` | `2026-08-12T10:47:21Z` |

工作树指纹按“相对路径、文件 SHA-256、文件大小”的稳定序列计算，并排除 `.git`、`.runtime`、`__pycache__`、`.pytest_cache` 与 `.pyc`。

## 3. Test Environment

- OS：Windows 10 build `10.0.26200`，AMD64。
- Python：3.11.15。
- 可用测试框架：pytest；外部临时目录需使用仓库外 `test_` 归档路径。
- Playwright：不可用；Selenium：不可用。
- 正式 Browser Harness 存在，但现有测试使用 `FakeBrowserAdapter`，属于 `CONTROLLED_RUNTIME`。
- 正式 Runtime 有 `ModelInvocationAdapter` 协议，但当前上下文没有可调用的 Host Model provider 或 Token telemetry adapter。
- 外部 Gold Case：`C:\Users\28388\Desktop\test_f14_r_real_gold_case_c_20260812\archive\`。

所有实际测试数据、冻结输入和归档报告均在 Skill 仓库之外；Skill 仓库只保留测试、Runtime、报告和既有最小修复。

## 4. Real Model Evidence

本轮没有发生可标记为 `REAL_MODEL` 的模型调用。原因是正式 Runtime 只定义了 `ModelInvocationAdapter` 接口，当前环境没有连接 Codex / Host Model 的真实适配器，也没有可读取 `input_tokens`、`cached_input_tokens`、`output_tokens`、`total_tokens` 的接口。

因此以下字段必须保持未知：

| 指标 | F13 | F14 | 结论 |
|---|---|---|---|
| `real_model_requests` | 0（本轮未执行） | 0（本轮未执行） | 不能推导减少 |
| `real_llm_invocations` | `unavailable` | `unavailable` | 未执行真实调用 |
| `input_tokens` | `unavailable` | `unavailable` | 未取得 |
| `cached_input_tokens` | `unavailable` | `unavailable` | 未取得 |
| `output_tokens` | `unavailable` | `unavailable` | 未取得 |
| `total_tokens` | `unavailable` | `unavailable` | 未取得 |

受控 Fixture、Deterministic callback、Fake Model 和本轮 Codex 交互本身都没有被当作 Real Model benchmark。

## 5. Real Browser Evidence

真实 Browser Gold E2E 未执行。仓库内 `runtime.browser` / Browser Broker / Browser Harness 的正式路径存在，且 Browser policy 仍只允许 Evaluator；但本机没有 Playwright/Selenium，当前工具上下文没有可调用 Browser connector。

已执行的 `tests/test_browser_harness.py` 共 6 项，以及与 F14 相关的受控 Browser Gate 测试，均使用 `FakeBrowserAdapter`。这些结果只证明 policy、lease、manifest、redaction 和 gate 逻辑，不证明页面加载、真实交互、布局、响应式、console error 或视觉质量。

`REAL_BROWSER = NOT EXECUTED`，`AUTOMATED_VISUAL_SCORE = NOT_AVAILABLE`。

## 6. Gold Case C Lifecycle

外部项目冻结的原始用户输入为：

> 我要做一个个人记账 App。

冻结输入和显式用户回答 Fixture 保存在：
`C:\Users\28388\Desktop\test_f14_r_real_gold_case_c_20260812\archive\gold_case_c_inputs.json`。

Fixture 覆盖需求澄清、设计方向选择、Prototype 确认、产品批准、Plan 批准、预算 Change Request 和 Change Request 批准。要求的正式路径是：First-Ask → Research Decision → Coverage/Gap → Sufficiency → 三方向 Design Exploration → Selected Prototype → Product/Plan Approval → Generator → Tests → Fresh Evaluator → Rework → Accepted → Change Request → Regression → Accepted。

但由于没有 Real Model provider 和 Real Browser，Gold Case C 的真实完整生命周期状态为 **NOT EXECUTED**。当前可引用的正式 Runtime 受控组合证据为 Stage 2、Stage 3、Stage 4B 和两阶段 Design Exploration；它们验证正式 CAS、Approval、Generator、Evaluator、Rework、Change Request 和回归路径，但不是这句原始用户输入驱动的真实 Host Gold E2E。

## 7. F13 vs F14 Matched A/B

本轮没有完成真实 F13/F14 Matched A/B，因为两侧都无法接入同一真实 Model family、settings、Browser scenario 和 Token usage API。Gold Case C 的冻结输入、路线、批准计划和 Change Request 已保存，但不能把“没有运行”写成 A/B 结果。

已有 F14 Final Qualification 的受控 Case C A/B 使用同一冻结输入和受控 Fixture，类型明确为 `CONTROLLED_RUNTIME`；本报告只将其作为历史受控证据引用，不升级其证据等级。

## 8. Context Efficiency

历史受控 Case C A/B 的净成本为：

| 指标 | F13 Full | F14 Selective | 差异 | 证据类型 |
|---|---:|---:|---:|---|
| Formal Context Bytes | 10000 | 10000 | 0 | `CONTROLLED_RUNTIME` |
| Initial Selective Bytes | — | 5200 | — | `CONTROLLED_RUNTIME` |
| Expansion Bytes | — | 2200 | — | `CONTROLLED_RUNTIME` |
| Recovery Bytes | — | 400 | — | `CONTROLLED_RUNTIME` |
| Net Context Bytes | 10000 | 7800 | -2200 / -22% | `CONTROLLED_RUNTIME` |
| Repeated Context Bytes | 6000 | 3500 | -2500 | `CONTROLLED_RUNTIME` |

这些是 Fixture bytes，不是 Token，也不是成本。F14-R 没有新的 Real Model Context measurement。

## 9. Actual Token Efficiency

Actual Token efficiency 为 **NOT PROVEN**。没有把 Context bytes 除以经验换算比例，也没有把受控数字冒充 Host usage。F13/F14 的 `input_tokens`、`cached_input_tokens`、`output_tokens`、`total_tokens` 都是 `unavailable`。

## 10. Model Request Efficiency

真实 Model request reduction 为 **NOT PROVEN**。本轮 `real_model_requests=0` 只是“没有执行真实调用”，不是“减少了 0 个”或“减少率为 100%”。Deterministic Python-only gate 的受控测试证明了 hash、schema、revision、approval、cache、manifest 和依赖 metadata 等工作可以被安全分类，但没有证明实际替代了多少 Host request。

## 11. Role Breakdown

真实 Role usage 没有样本，不能给 Planner、Generator、Evaluator 排出真实节省排名。

受控证据覆盖三个 Role：

- Planner：requirements、Design Exploration、Product/Plan Approval 输入边界受测。
- Generator：approved source chain、Implementation、Rework、Change Request Generator 受测。
- Evaluator：fresh invocation、Independent Context、broader regression boundary 和 Browser Gate 受测。

“Generator Rework 最值得继续优化”仍只是此前受控 telemetry 的方向判断，不是本轮 Real Model cost 结论。

## 12. Phase Breakdown

真实 Phase usage 不可用。受控测试覆盖 Requirement Discovery、Design Exploration、Planning、Generator Initial、Generator Rework、Evaluation、Change Request、Regression；但没有每个 Phase 的真实 input/output/cached token 记录。

| Phase | 真实 Token | 受控状态 |
|---|---|---|
| Requirement Discovery | `unavailable` | 正式路径受测 |
| Design Exploration | `unavailable` | 三方向与 selected prototype 规则受测 |
| Planner Revision | `unavailable` | 规则覆盖，真实 usage 未测 |
| Generator Initial | `unavailable` | Stage 2 受测 |
| Generator Rework | `unavailable` | Stage 3/F14-E 受测 |
| Evaluation / Regression | `unavailable` | Independent boundary 受测 |
| Change Request Generator | `unavailable` | Stage 4B 受测 |

## 13. Expansion Analysis

受控 Case C 的 Expansion 为 2200 bytes，Recovery 为 400 bytes；这两个数属于既有 `CONTROLLED_RUNTIME` Fixture。真实 Expansion count、Expansion tokens、Expansion loop frequency 和按 Role/Phase 分解均为 `unavailable`。

F14-D 的受控测试验证了 L1/L2/L3 渐进扩展、unknown dependency、authority uncertainty、revision mismatch、secret boundary、预算和 expansion loop recovery；失败时回到 F13 或 BLOCK，不继续猜测。

## 14. Fallback Analysis

真实 Fallback rate 为 `unavailable`，真实 `fallback_model_requests` 和 `fallback_tokens` 也为 `unavailable`，所以不能回答 Fallback 是否在生产频率下抵消收益。

受控测试覆盖的 Fallback 原因包括：unknown dependency、authority uncertainty、revision mismatch、coverage uncertainty、cache invalid、index invalid、security concern、context corruption、builder failure。相关测试均保持正式 F13 Context 或 BLOCK，未通过放宽安全标准降低 fallback。

## 15. Generator Rework Analysis

Stage 3 受控 E2E 已验证：Evaluator FAIL → Issue Package → Generator Rework → Evaluator 再验证 → ACCEPTED。F14-E 进一步验证了 changed unit reuse、依赖传播、unknown dependency fallback 和语义 summary 不替代正式 Context。

但 F14-R 要求的同一真实 Generator Rework Checkpoint 的 F13/F14 A/B 未执行，故以下指标全部为 `unavailable`：rework input tokens、rework repeated context、rework fallback tokens、fix latency 和 Real Model correctness delta。

## 16. Change Request Analysis

Stage 4B 受控 E2E 已验证：`ACCEPTED → CHANGE_REQUESTED → Impact Analysis → Change Approval → Generator → Regression → Evaluator → RELEASE_READY → ACCEPTED`。真实 Host Change Request A/B 没有执行，不能证明预算功能在真实 Model 下的 token 或 model work 节省。

## 17. Requirement Coverage

受控 F14 C/Post Verification、F14-D、F14-E 和 F14-C6 测试保持 Mandatory、Authority、Revision、Coverage 等约束；当前受测集合的 Critical False Omission 为 0。Gold Case C 的真实 First-Ask 需求发现没有发生，因此不能宣称“原始模糊需求已经被真实 Planner 完整覆盖”。

## 18. Design Quality

受控 Design Exploration 验证了 exactly 3 个方向、结构差异、选中 Prototype 独立轮次、Prototype Confirmation 和 Product Approval 不能被模糊肯定跳过。方向差异要求信息架构、导航、Dashboard composition、交互模型或数据可视化策略变化，而不是只换颜色。

真实 Browser 视觉质量未执行，故 layout、responsive behavior、visual hierarchy、empty/loading/error state 和 accessibility 基础检查均为 `unavailable`，没有伪造评分。

## 19. Approval Integrity

既有 P1 `DEF-F14-001`（批准来源无内容 SHA-256 绑定）和 `DEF-F14-002`（`approved_plan` 缺失时 TypeError）已最小修复。修复后受控 fault clone 为 7/7 fail-closed：缺少 approved plan、Plan hash mismatch、缺少 Product Approval、缺少 Prototype Confirmation、Requirements revision mismatch、Plan superseded、未授权工件修改。

真实 Canary 中的 Approval Integrity 未执行，故真实 bypass rate 为 `unavailable`；受控结果为 Approval Bypass 0。

## 20. Generator Correctness

受控 Stage 2/3/4B 通过，Generator 只能从 approved source chain 执行；缺失或篡改来源时 Gate 结构化拒绝。当前没有真实 Model 生成实现，因此无法判断真实 Model 是否会产生额外语义错误或真实 Rework 成本。

## 21. Evaluator Independence

受控 Runtime 验证了 fresh invocation、independent context、current revision、current code snapshot、runtime evidence、own verification 和 broader regression boundary。Evaluator 不继承 Generator reasoning、claimed coverage、self-evaluation 或 changed-files-only scope。

由于没有真实 Host invocation，本轮结论为 `CONTROLLED_PASS_ONLY`，不是 Real Model Level 1 证据。

## 22. Evaluator Detection Parity

历史受控 Case C A/B 的 `critical_detection_difference=0`，相关受控 Evaluator test 通过；本轮没有 F13/F14 Real Model A/B，也没有真实 Browser findings，因此 Real Detection Parity 为 `NOT PROVEN`。

任何未来 Real Model A/B 出现 Critical issue 漏检，都必须直接使 Global Rollout Gate 失败。

## 23. Security

受控 F14-D、F14-C6、F14-B7 和 source-chain fault injection 覆盖 secret scan、path escape、authority、revision、budget、unknown dependency、cache/index/manifest corruption。受测集合中 Security Miss 为 0；失败时 DENY、BLOCK 或 F13 fallback。

真实 Model Prompt/Tool interaction 与真实 Browser 网络边界没有执行，因此生产安全风险不能由本轮升级证明。

## 24. Privacy

受控 telemetry 不保存 secret、private reasoning 或 raw model output；Browser Harness 对可见文本和输入执行 redaction。受测集合中 Privacy Miss 为 0。

真实 Host usage 和真实用户数据没有进入本轮，因此不能把受控 privacy 结论外推到真实生产数据。

## 25. Regression

| 验证 | 结果 | Benchmark 类型 |
|---|---:|---|
| Full unittest | 497/497 PASS | `CONTROLLED_RUNTIME` |
| F14 focused pytest | 116 passed，16 subtests passed | `CONTROLLED_RUNTIME` |
| Stage 2/3/4B + Design | 10/10 PASS | `CONTROLLED_RUNTIME` |
| F14 A/B + Telemetry + Browser Harness | 11/11 PASS | `CONTROLLED_RUNTIME` |
| `git diff --check` | PASS | `SYNTHETIC` |

这证明当前受控 baseline 没有退化；它不证明真实 Model 或真实 Browser 质量 parity。

## 26. Crash / Recovery

受控 Session/Event/Checkpoint/Lease、Context corruption、Expansion loop、Incremental invalidation 和 F13 fallback 测试通过，没有观察到 state corruption 或 duplicate recovery event。真实 Host crash、Browser crash 和真实 Model retry 没有执行。

## 27. Fault Injection

| Fault | 期望行为 | 受控证据 | 结果 |
|---|---|---|---|
| Context cache stale | reread / F13 fallback | `test_f14_source_cache.py` | PASS |
| Index hash mismatch | BLOCK / rebuild | `test_f14_artifact_index.py`、`test_f14_b7_safety.py` | PASS |
| Incremental dependency unknown | F13 fallback | `test_f14_e_incremental_context.py` | PASS |
| Required artifact missing | BLOCK / fail closed | `test_f14_c4_enforced_gate.py` | PASS |
| Approved artifact tampered | DENY | `test_approval.py`、source-chain clone | PASS |
| Revision stale | DENY / fallback | `test_f14_d_context_escalation.py` | PASS |
| Manifest corruption | BLOCK / rebuild | `test_f14_e_incremental_context.py` | PASS |
| Expansion incomplete | recovery / F13 | `test_f14_d_context_escalation.py` | PASS |
| Selective Builder exception | F13 fallback | `test_f14_c4_enforced_gate.py` | PASS |
| Invocation Gate ambiguity | LLM_REQUIRED / BLOCK | `test_f14_f_context_and_invocation.py` | PASS |

这些 Fault Injection 是 `CONTROLLED_RUNTIME`，不是 Real Model 或 Real Browser fault injection。

## 28. Browser QA

正式 Browser Harness 的 policy、capability、lease、manifest、redaction、scenario mapping 和 BLOCK 状态测试通过；但 Fake adapter 没有加载真实页面，也没有执行记账 App 的新增交易、编辑、删除、分类、收入/支出、月度汇总、图表、月度预算和预算进度。

Browser Blocking Failure、视觉退化、console error、broken links、responsive behavior 和 accessibility 在真实 Gold Case 中均为 `unavailable`，不是 0。

## 29. Product / Test / Environment Failure Classification

| 分类 | 事件 | 结论 |
|---|---|---|
| PRODUCT DEFECT | F14 Final Qualification 的 DEF-F14-001/002 | 已修复并复验；本轮未发现新 P0/P1 |
| TEST DEFECT | 本轮无新的测试逻辑缺陷 | 0 |
| ENVIRONMENT FAILURE | 受限沙箱无法创建系统临时目录 | 升级到外部 test_ 目录后通过 |
| REAL MODEL CAPABILITY BLOCK | 无 Host Model provider/usage adapter | F14-R blocked |
| REAL BROWSER CAPABILITY BLOCK | 无 Playwright/Selenium/Browser connector | Real Browser blocked |
| CONTROLLED ADAPTER LIMITATION | Fake/Fixture adapter 无真实语义和 usage | 不计为 Real Model |
| REAL MODEL VARIANCE | 未执行真实调用 | 未观察，不能推断 |
| EXPECTED SECURITY BLOCK | 非法路径、secret、stale、unknown、越权 | 受控 DENY/BLOCK/FALLBACK |
| UNKNOWN | 未归因产品结果 | 0 |

## 30. Real Model Variance

本轮没有 Real Model run，因此没有 `REAL_MODEL_VARIANCE` 样本。不能把受控 Fixture 的稳定结果解释成模型稳定性，也不能把随机性归因给 F14 架构。

## 31. Net Benefit Attribution

本轮可支持的收益只有历史 `CONTROLLED_RUNTIME` 证据：净 Context bytes 减少 2200（22%）、重复 Context 减少 2500、受控 Runtime reuse 和安全 fallback 路径成立。

本轮不能支持的收益包括：Actual Token reduction、Cached Input Token reduction、Output Token reduction、Total Lifecycle Token reduction、Real Model Request reduction、Real LLM invocation reduction、生产 fallback rate 和真实 Browser quality parity。

## 32. Remaining Evidence Gaps

1. 没有真实 Codex / Host Model adapter 和完整 usage telemetry。
2. 没有真实 Gold Case C 从固定原话开始的 Host lifecycle。
3. 没有真实 Browser Gold E2E 和视觉/交互证据。
4. 没有同一冻结 Checkpoint 的 Real Model F13/F14 Rework A/B。
5. 没有真实 Change Request A/B。
6. 没有生产频率的 Expansion、Fallback、Recovery 样本。
7. 没有 Real Model variance 样本。

## 33. Global Rollout Gate

| Gate | 结果 |
|---|---|
| Real Gold E2E completed | **BLOCKED** |
| Real Browser Gold E2E completed | **BLOCKED** |
| No unresolved P0/P1 | PASS（受控范围） |
| Critical False Omission = 0 | PASS（受控范围） |
| Critical Detection Difference = 0 | PASS（受控 A/B） |
| Approval Bypass = 0 | PASS（受控 fault injection） |
| Security Miss = 0 | PASS（受控测试集） |
| Privacy Miss = 0 | PASS（受控测试集） |
| Regression parity | PASS（受控回归） |
| F13 fallback verified | PASS（受控） |
| Actual real-model efficiency measured | **BLOCKED** |
| Net lifecycle model cost lower | **NOT PROVEN** |
| Fallback acceptable at production frequency | **NOT PROVEN** |

Global Default Gate 未满足。按照安全优先原则，继续保持 F13。

## 34. Final Recommendation

最终只推荐：

```text
BLOCKED — INSUFFICIENT REAL-MODEL EVIDENCE
```

建议保持：

- `context_delivery_mode: f13_full`
- Global Selective Context：OFF
- Evaluator Selective Context：OFF
- Global Invocation Gate：OFF
- Evaluator Full / Independent Context
- F13 fallback：保留

待具备真实 Host Model usage adapter、真实 Browser connector 和用户单独批准的明确 Canary Session 后，才能重新执行 F14-R。即使未来获得 FULL PASS，也不能自动修改全局默认。

## 35. Stop Boundary

本轮在报告生成、JSON 校验、外部 test_ 工件归档和默认配置复核后停止。没有修改全局 F14 开关，没有删除 F13 fallback，没有开启 Evaluator Selective Context，没有提交 Git、push、PR 或创建 F15 文件。

## 36. 对 30 个核心问题的逐项回答

1. F14 是否完成真实 Model Gold E2E？**没有；`REAL_MODEL = NOT EXECUTED`。**
2. 是否完成真实 Browser Gold E2E？**没有；`REAL_BROWSER = NOT EXECUTED`。**
3. F13 实际 input tokens 是多少？**`unavailable`。**
4. F14 实际 input tokens 是多少？**`unavailable`。**
5. Cached input token 是否变化？**无法判断；两侧均 `unavailable`。**
6. Output token 是否变化？**无法判断；两侧均 `unavailable`。**
7. Total lifecycle token 是否下降？**未证明。**
8. Real Model Request 是否下降？**未证明；本轮没有真实调用。**
9. Python-only work 实际替代了多少 Model 调用？**真实数量 `unavailable`；受控测试只证明可分类，不证明 Host request 替代量。**
10. Context bytes 实际减少多少？**受控历史 A/B 净减少 2200 bytes（22%）；真实 Model Context 未测。**
11. Repeated Context 减少多少？**受控历史 A/B 减少 2500 bytes；真实 Model 未测。**
12. Expansion 占用了多少额外成本？**受控历史 Fixture 为 2200 bytes；真实 Token/Model cost `unavailable`。**
13. Fallback 占用了多少额外成本？**真实成本 `unavailable`；不能用受控路径数量换算。**
14. Fallback rate 是多少？**真实 rate `unavailable`。**
15. 哪些 Phase fallback 最频繁？**没有真实样本，不能排名。**
16. 哪些 Role 节省最多？**没有真实样本，不能排名。**
17. Generator Rework 是否明显受益？**受控路径通过；真实 Token/Model 受益未证明。**
18. Planner Revision 是否受益？**受控规则覆盖；真实 usage 未证明。**
19. Change Request 是否受益？**受控 Stage 4B 通过；真实 A/B 未执行。**
20. Evaluator 检测能力是否保持一致？**受控 A/B critical detection difference 为 0；真实 parity 未证明。**
21. 是否出现 Critical False Omission？**受控测试集为 0；真实 Gold 未执行。**
22. 是否出现 Approval Bypass？**受控 fault injection 为 0；真实 Canary 未执行。**
23. 是否出现 Security Miss？**受控测试集为 0；真实生产边界未证明。**
24. 是否出现 Privacy Miss？**受控测试集为 0；真实用户数据未进入本轮。**
25. Browser QA 是否出现质量下降？**无法判断；真实 Browser 未执行。**
26. Design Exploration 是否出现设计退化？**受控结构差异通过；真实视觉质量未证明。**
27. F13 fallback 是否可靠？**受控测试通过；真实 Host failure 未验证。**
28. 节省是否被 Expansion / Fallback 抵消？**真实频率未测，不能判断。**
29. F14 是否已经值得成为 Global Default？**不值得；Global Gate 未满足。**
30. 是否允许开始 F15？**NO。F14-R 不是 FULL PASS，且用户未明确批准 F14 Freeze/Global Rollout。**
