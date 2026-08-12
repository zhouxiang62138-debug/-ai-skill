# F14-F Controlled Context Optimization & Invocation Gate 报告

报告日期：2026-08-12  
报告范围：F14-F0 Preflight、F1 Generator Rework Canary 基础、F2/F3/F4/F5 的受控 Context 闭包、F6 Deterministic Invocation Gate 基础  
最终状态：**CONDITIONAL PASS**  
运行策略：默认 `f13_full`；未进行全局 Selective Context 或 Invocation Gate rollout。

## 1. 结论

F14-F 的安全控制面已实现并通过 controlled fixture 验证：

- F14-G 五个冻结 Baseline ID 已校验：`F14-G-BASELINE-001`～`005`。
- Feature Flag 默认关闭，只有 test_ 项目、Benchmark 或明确 Canary 才允许 Selective Context。
- Selective Context 的 mandatory coverage、authority、revision、unknown、conflict、stale、role scope、E1、path、capability、secret、security、privacy 和 approved scope 均为硬门禁。
- Context 成本按 `initial selective + expansion + recovery/additional` 计算 Net Context，不使用 Candidate Reduction 冒充最终节省。
- Fallback 与 BLOCK 已分离：优化故障回退 F13；权威性、覆盖或安全失败 BLOCK。
- Invocation Gate 只把可完全复现的确定性 Python 工作分类为 `PYTHON_ONLY`；语义任务保持 `LLM_REQUIRED`。
- Evaluator verdict、Generator 输出、Planner 产品/需求判断不复用；Evaluator 保持 fresh/independent 约束。
- Lifecycle audit record 保留，且不与真实模型请求数量混淆。

## 2. F14-G 基线冻结

F14-G 报告中已有以下固定基线，本轮只做校验，不覆盖基线文件：

| Baseline | 状态 |
| --- | --- |
| `F14-G-BASELINE-001` | 冻结并可读取 |
| `F14-G-BASELINE-002` | 冻结并可读取 |
| `F14-G-BASELINE-003` | 冻结并可读取 |
| `F14-G-BASELINE-004` | 冻结并可读取 |
| `F14-G-BASELINE-005` | 冻结并可读取 |

每次 A/B 比较必须绑定同一个 Baseline ID、相同输入 fingerprint、相同 revision、fixture、evaluation profile、environment policy 和 config hash。

## 3. Feature Flag 与启用范围

配置文件：`config/f14.yaml`

默认值：

```yaml
f14:
  context_delivery_mode: f13_full
  selective_context:
    enabled: false
  evaluator_selective_context:
    enabled: false
  invocation_gate:
    enabled: false
```

当前正式配置没有启用任何 Role/Phase。代码支持以下受控范围：

- Generator：`rework`、`resume`、`initial_implementation`、`change_request`
- Planner：`planning_revision`、`change_impact`、`product_revision`
- Evaluator：`evaluation`、`regression`，但需额外通过独立 Evaluator Canary 开关

关闭开关即可恢复 F13 Full Safe Context，不需要 migration rollback。

## 4. Controlled Context 成本证据

受控单元测试使用固定 fixture：

| 项目 | Bytes |
| --- | ---: |
| F13 Full Context | 1000 |
| Initial Selective Context | 400 |
| Expansion | 100 |
| Recovery / Additional Context | 50 |
| Net Context | 550 |
| Gross Reduction | 60% |
| Net Reduction | 45% |

该数值是 controlled fixture 证据，不是生产项目平均值，也不是 Host/Codex token 使用量。Telemetry 现在分别记录 Candidate、Formal Selective、Expansion、Net Reduction 和 F13 fallback。

## 5. 阶段状态

| 阶段 | 状态 | 说明 |
| --- | --- | --- |
| F0 Preflight | PASS | 基线、默认 flag、文档一致性和现有 focused 基线已检查 |
| F1 Generator Rework Canary | PASS（受控） | Generator Rework/Resume mandatory scope、Selective 派生和回退已验证 |
| F2 Initial Implementation | CONDITIONAL | Mandatory Closure 与 approved reference binding scope 已定义；未全局启用 |
| F3 Change Request | CONDITIONAL | Change-focused scope、原核心行为和 regression boundary 已定义；未全局启用 |
| F4 Planner | CONDITIONAL | 仅允许 planning revision/change impact/product revision；First Ask 和早期探索保持宽 Context |
| F5 Evaluator | CONDITIONAL | 独立 mandatory coverage、broader regression boundary 和 detection parity 已实现；Evaluator Selective 默认关闭 |
| F6 Invocation Gate | PASS（受控） | Python-only / LLM-required / BLOCKED 分类和生命周期记录已验证 |
| F7 A/B | PASS（controlled） | 固定 Baseline、输入 fingerprint、净 Context 与 Detection Parity 比较器已验证 |
| F8 Fault/Rollback | PASS（受控） | 优化异常回退 F13；权威/覆盖/安全异常 BLOCK；flag 可即时关闭 |
| F9 Final Evaluation | CONDITIONAL | 受限于没有真实 Host model benchmark，未宣称真实 token 或生产 rollout |

## 6. 对最终问题的逐项回答

1. **哪些 Role/Phase 启用 Selective Context？** 当前正式配置没有启用；受控实现支持 Generator Rework/Resume/Initial/Change Request、Planner 修订类阶段，Evaluator 需独立开关。
2. **Feature Flag 默认是什么？** `context_delivery_mode: f13_full`，Selective 和 Invocation Gate 均为 `false`。
3. **Generator Rework 的 F13 Full Bytes？** 由当前 F13 Package 的 `inline_bytes` 记录；controlled fixture 为 1000。
4. **Generator Rework 的 Net Selective Bytes？** controlled fixture 为 550。
5. **Net Reduction？** controlled fixture 为 45%。
6. **Expansion Rate？** controlled fixture 为 `100 / 400 = 25%`。
7. **Fallback Rate？** 受控故障路径逐次记录 `fallback_f13`；当前没有可代表生产流量的 fallback 分母，因此不虚构生产百分比。
8. **Generator Initial Implementation 结果？** 已实现较宽 Mandatory Closure：Approved Plan、Product Spec、Requirements、AC、Constraints、Architecture/Data Contract、Relevant Code/Tests 和 Approved Reference Binding；保持 Canary-only。
9. **Change Request 结果？** 已实现围绕 Approved Change Items、Affected Requirements/AC/Code、Regression Boundary 和 Global Constraints 的 Context scope；保留原核心行为约束。
10. **Planner Selective Context 结果？** 仅支持 Planning Revision、Change Impact Analysis 和既有 Requirements 的 Product Revision；First Ask、Requirements Discovery、早期产品/创意探索不进入 Selective。
11. **Evaluator Detection Parity？** A/B 比较器逐项比较 Detected Issues、Critical Issues、Security Findings、Browser Findings，而不是只比较最终 PASS/FAIL。
12. **Evaluator 是否 fresh / independent？** 是。Gate 拒绝复用旧 verdict；既有 E1 独立 Context 和 fresh invocation 约束继续有效。
13. **Candidate Miss 是否发生？** 受控负向测试会将缺失 Mandatory 转为 BLOCKED；优化器异常转为 Fallback F13，没有静默猜测。
14. **Critical False Omission？** controlled gate 为 0；生产值需来自真实 Evaluation evidence。
15. **False Mandatory？** controlled gate 为 0。
16. **False Block？** controlled gate 为 0；真实覆盖或安全失败仍应 BLOCK，不能把正确 BLOCK 当作 false block。
17. **Security / Privacy Miss？** controlled gate 为 0；Secret、Path、Capability 和 Security/Privacy 字段均进入 Selective 硬门禁。
18. **重复 Context 减少多少？** A/B 比较器支持 `repeated_context_bytes`；当前 controlled benchmark fixture 从 500 降到 100，减少 400 Bytes。
19. **哪些 Python-only Invocation 被移除？** State/schema validation、revision、CAS precheck、approval chain validation、artifact lookup、hash、diff、test parsing、browser deterministic evidence parsing、dependency validation、context coverage、workflow transition、cache validation、baseline/manifest validation。
20. **Real Model Requests 是否减少？** 代码支持比较该指标，但当前 F14-G 和本轮 controlled fixture 的 Real LLM Invocation 都是 0，因此不宣称真实模型请求已减少。
21. **哪些 Invocation 明确保留？** Semantic understanding、ambiguity、creative judgment、implementation、architecture reasoning、evaluation judgment、product decision、bug diagnosis、design review、Generator 输出、Planner 决策和 Evaluator verdict；Evaluator 每轮 fresh。
22. **Semantic Task Misclassification 是否为 0？** controlled 负向测试覆盖 Product Decision、Bug Diagnosis、Evaluation Judgment，均未被分类为 Python-only。
23. **F13 fallback 是否可靠？** 是。Selective builder/coverage/依赖处理异常返回 F13；authority、coverage、state 或 security 失败返回 BLOCKED，区分了优化失败与权威失败。
24. **A/B Quality 是否与 baseline 一致？** controlled parity 测试要求 mandatory coverage、AC、security、privacy、regression、browser 和 issue detection 均一致；差异即 FAIL。
25. **Controlled Net Context Reduction？** controlled fixture 为 45%。
26. **Actual Host Token 是否可获得？** 当前不可获得；F14-G 报告已明确 Real LLM Invocation 为 0。
27. **是否避免虚构 Token？** 是。Telemetry 将 token 标记为 `unavailable`，不会使用 `context bytes / 4` 伪造 Codex/Host token。
28. **Existing Regression？** 本轮 focused F14/C/D/E/G 回归为 73 passed、16 个 subtests passed；完整 `unittest discover` 为 497/497 PASS。
29. **P0/P1？** 当前已知 P0/P1 为 0；保留一个受控交付边界：未接入真实 Host model benchmark，因此不能升级为生产 rollout。
30. **F14-F 最终 PASS / CONDITIONAL PASS / FAIL？** `CONDITIONAL PASS`：安全默认路径、受控 Canary、Fallback、BLOCK、A/B parity 和 Invocation classification 已通过；真实 Host token、全阶段生产流量和全局 rollout 尚未授权/验证。

## 7. 验证记录

本轮执行：

```text
F14-F focused + F14-C/D/E/G related: 73 passed, 16 subtests passed
Full unittest discovery: 497/497 PASS
git diff --check: PASS
```

环境说明：pytest/unittest 需要仓库外可写临时目录；默认临时目录在本运行环境中不可用，因此测试使用了 `C:\Users\28388\Desktop\f14-g-run-temp`。这属于执行环境约束，不计为产品回归。完整 unittest discovery 已成功完成。

## 8. 停止边界

本轮在 F14-F 结束后停止：

- 不修改 Project Effort Profile。
- 不自动进入下一阶段 Runtime。
- 不把 `f14_selective_canary` 改成所有项目默认值。
- 不宣称真实 Codex/Host Token Reduction。
- 不删除 F14-G 或任何历史测试、报告和 Baseline 记录。
