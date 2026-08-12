# F14-G Telemetry & Baseline Benchmark Report

报告阶段：F14-G Full Telemetry + Baseline Benchmark
行为约束：Behavior-neutral
正式 Context：F13 Full Safe Context
生成日期：2026-08-12

## 1. 结论

F14-G：PASS。

F14-F：GO（仅表示具备进入下一阶段的证据，不自动启用 Invocation Gate、Selective Context、Invocation 减少或任何模型结果复用）。

本轮没有修改正式执行策略：模型边界仍按现有 Runtime 执行，Candidate Context 只作为 Shadow Evidence；没有启用 Invocation Gate、Selective Context、LLM Semantic Result Reuse，也没有改变 Approval、E1、Mandatory Regression 或 Coverage Gate 语义。

## 2. 验证证据

| 验证项 | 结果 |
| --- | --- |
| F14-G focused pytest | 3/3 PASS |
| F14-G + F14-B Telemetry focused pytest | 6/6 PASS |
| F14-B～F14-E focused pytest | 98 passed，16 subtests passed |
| Full unittest discovery | 497/497 PASS |
| `git diff --check` | 待最终工作树复核 |
| Benchmark Harness | A～E 五个 `test_` 项目均 PASS |
| Benchmark 类型 | Controlled Benchmark；不是 Real Model Benchmark |
| Real LLM Invocation | 0（本轮没有接入真实模型） |

## 3. Telemetry 覆盖范围

已实现并版本化记录以下数据：

- G1 Runtime Efficiency：文件读取、读取字节、Hash、目录扫描、Parser、Artifact/Dependency/Diff Index、Source Cache、Full/Incremental Rebuild 和 Runtime latency。
- G2 Context Efficiency：F13 正式 Context、Shadow Candidate、Mandatory、Task-relevant、On-demand、Omitted、Unknown、复用/重建 Unit 与 Bytes、Fallback F13、Candidate Reduction。
- G3 Model Efficiency：实际模型边界请求、Role Invocation、Retry、Fallback、Perception、Rollover、Python-only，并按 Role/Phase 归因。
- G4 Quality：Mandatory 覆盖、False Omission、False Mandatory、False Block、Approval Chain、Evaluator Independence、AC、Regression、Browser、Fallback 和 Context Expansion 结果。
- F14-D Escalation：Request、Approved、Denied、Unavailable、Cycle、Stale、Unauthorized、Expansion Round、L1/L2/L3、Fallback F13。
- F14-E Incremental：Manifest/Delta、Reuse、Rebuild、Invalidation、Stale、Unknown、Summary 和 Full Safe Fallback。
- Context Duplication：重复 Source Delivery、重复 Bytes、Unchanged Source/Unit Redelivery。

Telemetry 是 append-only、schema-versioned、non-fatal 的安全摘要；不修改 `project.yaml`，不决定 Workflow State 或 Evaluation PASS/FAIL，不保存模型原文、完整工具日志、Secret 或 private reasoning。

`schema_version` 保持 F14-B 的值 `1` 以兼容旧消费者；F14-G 扩展通过 additive 字段 `contract_version: 2` 表示。

## 4. A～E Controlled Baseline

固定输入包括同一案例的初始请求、approved requirements、approved plan、project revision、model id、evaluation profile、test commands、browser scenarios、environment policy 和 config hash。五个项目均使用 `test_` 前缀，并存放在 Skill 仓库外的临时目录。

| Case | 定位 | Baseline ID | Controlled model requests | Real LLM | Actual token usage | Shadow Candidate 理论缩减 |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| A | Simple / Simple | `F14-G-BASELINE-001` | 4 | 0 | 500 | 10% |
| B | Simple Product + Showcase Design | `F14-G-BASELINE-002` | 5 | 0 | 625 | 35% |
| C | Standard Product | `F14-G-BASELINE-003` | 6 | 0 | 750 | 20% |
| D | Complex Business | `F14-G-BASELINE-004` | 6 | 0 | 750 | 8% |
| E | Low Complexity / High Risk | `F14-G-BASELINE-005` | 6 | 0 | 750 | 15% |

合计 Controlled model-boundary requests 为 27，Real LLM Invocation 为 0；Shadow Candidate 平均理论缩减为 **17.6%**。这不是生产节省量，也不是 F14-F 已经启用的效果，只是候选空间证据。

### 4.1 Baseline hashes

| Baseline | Project tree hash | Fixture hash | Inputs fingerprint |
| --- | --- | --- | --- |
| `F14-G-BASELINE-001` | `7b3237086ff228697a8f6caf121f08d902dd27d7f2d4b3dde6bb0d4e44d43546` | `af74cf7c58b742841767905dac9859df721111beeb8be8548125666abc8d345d` | `baf5220a657b3eed6d1eb1b2b47c2b9e96100880c1338fbb87d4e5ca7d05de0c` |
| `F14-G-BASELINE-002` | `bf718c8da6e58f24b2f7bbceddfbbd199f8ebcbf106a09c75251ba43f847b54d` | `ac56b01739fb2066bfe0a3e67fe5fbbbcf8281ff889ca1a10789a3ca99353542` | `effc706bb7b9abb85aad678890aac95464bacbd55516ead7e3eb433464341af6` |
| `F14-G-BASELINE-003` | `844e2bb436737223bb7a95d7800fc550ea0ee9d81c345a2a3adeac7b58db5144` | `20771171ca196fd3564f365550f92fcda1d30fa03ee14af9d90fab987b28684b` | `8bb428dd2321149e8246bf6bd4f2b3c53603b0b1b857c2010fc854f30418397c` |
| `F14-G-BASELINE-004` | `a7f31b241a97557329e977e1cbff7cbacb29cda2f19515b5b2de0fb82b85d348` | `69a51b06e534512d9693dd6338f9c62ccc87f49ac00909d9d181745feb9e884a` | `ac7467cc8c77f00302526a347122ff81a0248944dc646f2b46c5952ce6d0cbf0` |
| `F14-G-BASELINE-005` | `4e23229965be83492a27d2fbfa66630cb5ecd4096fa0c4c4dd068d88f33e3450` | `deaf39ade03a4897a09beaa1a54c133a5888da096c4e57e3a9fdcc95fc4775b1` | `c0f6cc7c203f8bf12ae1799bb63536728d79f012408b1c0f6a5e664ebcc5c0be` |

所有 Baseline 的 Runtime schema 为 `13`，model id 为 `f14-g-controlled-fake-model`，evaluation profile、environment policy 和 config hash 在 A～E 中固定。

## 5. Role / Phase 成本

本轮为 Controlled Benchmark，token 是受控 fake adapter 明确提供的 actual fixture usage；不能据此宣称真实 Host/Codex 的 token usage。真实模型接入时，如果 Host 无法提供 `input_tokens`、`cached_input_tokens`、`output_tokens`，Telemetry 必须标记 `unavailable`，不能用 `context bytes / 4` 冒充 token。

| Case | Planner requests / tokens | Generator requests / tokens | Evaluator requests / tokens | 最重 Phase 依据 |
| --- | ---: | ---: | ---: | --- |
| A | 2 / 250 | 1 / 125 | 1 / 125 | Planner 250 tokens |
| B | 3 / 375 | 1 / 125 | 1 / 125 | Planner 375 tokens |
| C | 3 / 375 | 2 / 250 | 1 / 125 | Planner 375 tokens；Generator 有 Rework |
| D | 3 / 375 | 2 / 250 | 1 / 125 | Planner 375 tokens；Change Request/Rework |
| E | 3 / 375 | 2 / 250 | 1 / 125 | Planner 375 tokens；高风险 Research/Rework |

从可优化机会看，单纯按总 Role 切分会误导；C～E 的 Generator Rework、D 的 Change Request，以及各 Phase 重复交付的 Context 更值得在 F14-F 单独比较。

## 6. Context Duplication、Resume/Rework 与 Change Request

Controlled fixture 中：

- C：3 次重复 Source Delivery、900 重复 Bytes、2 次 Unchanged Source、2 次 Unchanged Unit。
- D：5 次重复 Source Delivery、1400 重复 Bytes、4 次 Unchanged Source、3 次 Unchanged Unit；同时覆盖 Change Request + Rework 路径。
- E：3 次重复 Source Delivery、900 重复 Bytes、2 次 Unchanged Source、2 次 Unchanged Unit。
- C～E 合计：11 次重复 Source Delivery、3200 重复 Bytes、8 次 Unchanged Source、7 次 Unchanged Unit。
- Resume/Rework 的可见机会集中在 C、D、E 的 Generator rework；F14-E 增量 Manifest 会记录 Reuse/Rebuild/Invalidation/Fallback，但本轮不改变正式 Context。
- Change Request 的可见机会集中在 D；它只记录重复加载与 Change Context 证据，不绕过 Change Request、Approval 或 Regression。

## 7. Quality Baseline 与 Escalation

五个 Controlled Case 的质量 Gate 均 PASS：

| 指标 | 基线 |
| --- | ---: |
| Mandatory covered / total | 4 / 4 |
| Required AC covered / total | 3 / 3 |
| Critical False Omission | 0 |
| False Omission | 0 |
| False Mandatory | 0 |
| False Block | 0 |
| Approval Chain Failure | 0 |
| Evaluator Independence Failure | 0 |
| Regression Failure | 0 |
| Browser Gate Failure | 0 |
| Security / Privacy Constraint Failure | 0 / 0 |

本次实际生产输入仍为 F13 Full Context，因此 L1/L2/L3 只属于 Shadow / Controlled Benchmark Evidence；报告不能宣称生产模型已经按 L1/L2/L3 运行。F14-D 的 Runtime Escalation 仍然保留授权、Cycle、Stale、Unknown、预算和 F13 Recovery 记录。

## 8. 对 25 个必答问题的直接回答

1. 一次完整项目的成本由 Runtime 确定性工作、Context 构建/重复交付、Model Request、Role/Phase、Rework/Resume、Change Request 和 Evaluation/Regression 共同组成，不是单一 Token 数。
2. Runtime 确定性成本由 G1 计数器记录；本轮 schema 已覆盖读取、Hash、Parser、Index、Cache、Diff、Rebuild 和 latency。
3. Context 实际交付由 `current_f13` 的 source count/bytes 与每次 Phase 的 Context bytes 记录。
4. 重复 Context 由 `context_duplication` 和 F14-E 的 unchanged source/unit 记录。
5. 未变化 Context 由 Source hash、Manifest、Reuse status 和 Unchanged counters 记录。
6. 真实模型请求只能来自模型适配器边界；controlled fake 请求与 Real LLM Invocation 已分离。
7. Planner、Generator、Evaluator 按 `model_efficiency.by_role` 和 `phase_cost` 分开统计。
8. Phase 成本按 `phase_cost` 记录；本轮最重是 Planner，C～E 的 Rework/Change Request 是更重要的优化切入点。
9. Shadow Candidate 平均理论可减少 17.6% Context；该值不等于生产节省。
10. Escalation 由 F14-D 统计 requests/approved/denied/unavailable、L1/L2/L3、cycle/stale/unauthorized 和 fallback。
11. 质量指标没有下降；五类 Controlled Case 的 Critical False Omission、False Block、Approval Chain Failure、Evaluator Independence Failure 均为 0。
12. Real LLM Invocation Count 为 0；本轮没有真实模型连接。
13. 当前 controlled fixture 提供了 actual token usage；真实 Host 不可提供时标记 `unavailable`，部分字段可用但不完整时标记 `estimated`。
14. Baseline ID 为 `F14-G-BASELINE-001` 至 `F14-G-BASELINE-005`。
15. Baseline hashes 已在第 4.1 节冻结。
16. F14-F 最大安全优化机会在 Generator 的重复 Context、Rework/Resume 和 Change Request Context；其次是高展示设计 B 的候选缩减空间。
17. 不能优化的 Invocation 包括真实模型边界本身、Evaluator 独立 fresh invocation、Approval/E1/Mandatory Regression 所需调用，以及质量或安全证据不完整时不能省略的调用。
18. F14-F 最终建议为 GO，但必须另行授权；F14-G 不自动进入 F14-F。
19. Telemetry Behavior-neutral：是。
20. Runtime Efficiency baseline：字段已覆盖，具体生产数值必须来自真实 Runtime Session；Controlled fixture 不虚构生产读取量。
21. Context Efficiency baseline：五案 Shadow Candidate 理论缩减平均 17.6%，正式输入仍为 F13 Full。
22. Model Efficiency baseline：27 次 controlled boundary request、0 次 Real LLM Invocation；controlled token 合计 3375。
23. Quality baseline：所有 Controlled quality gates PASS，Critical False Omission = 0，Evaluator Independence Regression = 0。
24. F14-F 应优先比较 Generator 的重复 Context、Rework/Resume 和 D 的 Change Request。
25. 最终 GO/NO-GO：F14-G = GO；F14-F = GO（待用户单独授权，当前不启用）。

## 9. 停止边界

F14-G 完成后停止。不会自动启用 Invocation Gate、缩减正式 Context、减少 Evaluator 调用、缓存 Planner/Generator/Evaluator 语义输出或改变 Project Effort Profile；这些都需要用户单独授权。
