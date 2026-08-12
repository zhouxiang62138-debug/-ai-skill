# F14-E Incremental Context Report

报告日期：2026-08-12  
阶段：F14-E — Incremental Context  
结论：**PASS**  
停止边界：本阶段完成；不进入 F14-G、F14-F、Project Effort Profile，也不启用 Invocation Gate。

## 1. 范围与不变量

本阶段实现的是确定性、无损的增量 Context 基础设施。缓存、Manifest、Delta 和 Summary 都是可重建派生数据，不成为 `project.yaml` 或 Session 业务状态的第二来源。

保持不变的边界：

- F13 Full Safe Context 仍是正式 Model Context；
- 默认 Selective Context 未启用；
- Planner、Generator、Evaluator 的正式 LLM Invocation 数量与语义未改变；
- Evaluator 仍保持独立 Role Scope、当前 Revision 和独立证据；
- User Explicit Requirement、Approved Requirement、Product Spec、Approved Plan、Change Scope、安全/隐私/监管约束和 Evaluator required evidence 仍以原始来源为 authority；
- Summary 不能替代权威原文；Semantic Summary 只能 Shadow / Navigation / On-Demand Hint；
- 缓存不确定、依赖未知、覆盖不完整或持久化失败时，结果要求 Full Safe fallback。

## 2. E1：Context Unit Manifest

新增 `IncrementalContextUnit` 与 `IncrementalContextManifest`。每个 Unit 记录：

- `id`、`authority`、`source_ref`、`exact_locator`；
- `source_hash`、`project_revision`、`policy_hash`、`role_scope`；
- `parser_version`、`summary_version`；
- `dependency_hash`、`delivery_hash`；
- `reuse_status`、`invalidation_reason`。

支持的状态为：`REUSED_EXACT`、`REBUILT`、`INVALIDATED`、`STALE`、`UNKNOWN`、`NOT_CACHEABLE`。

Unit 是 Requirement、AC、Constraint、Plan Task、Issue、Code/Test、Evidence 或 Artifact 等最小增量单位；没有使用整包 `generator_context_v12.bin` 式缓存。

## 3. E2：Dependency-aware Invalidation

`DependencyAwareInvalidator` 只沿显式 Dependency Graph 边传播影响。支持：

```text
REQ → AC → Plan Task → Code Unit → Test Unit
```

未知边不会被解释为“无影响”，而会生成 `UNKNOWN` 并设置 `fallback_required=true`。Global Security、Privacy、Regulatory Constraint 独立参与失效；Constraint 变更不会因为 Task Graph 未变化而继续复用。

Revision、Policy Hash、Parser Version、Summary Version、Dependency Hash、Delivery Hash 或 Source Hash 不匹配时，Unit 会重新构建或失效。Revision 变化默认保守重新验证，不假设内容相同就安全复用。

## 4. E3/E4：Diff-first 与 Resume

`IncrementalContextBuilder.build_from_diff_index()` 消费已有 F14-B Diff Index，将 changed files、changed artifacts、显式受影响节点和 unknown impact 节点转成增量输入，不根据文件名或自然语言猜依赖。

`ContextBuilder` 新增旁路接口：

- `build_incremental_manifest()`：从当前 F13 Package 生成 Unit Manifest 与 Context Delta；
- `build_incremental_resume()`：继续生成 Full Safe Package，同时计算可审计增量结果。

正式 `build()` 返回的 F13 Package 没有改成 Selective Context。Resume 复用前仍绑定 source identity、validated fingerprint、revision、policy、role、parser/version、dependency 和 delivery 条件。

F13 Builder 的文件来源接入了 `SourceCache`：未受保护的稳定来源可通过 metadata + content-addressed cache 复用；受保护/批准/安全敏感来源仍执行可信 hash 校验。Path Policy、Capability、Secret Scan 和 authority 校验没有被绕过。

## 5. E5：Context Delta

新增 `ContextDelta`：

```yaml
context_delta:
  base_manifest_hash:
  current_revision:
  reused_units:
  added_units:
  changed_units:
  invalidated_units:
  removed_units:
  unknown_units:
  coverage_result:
  fallback_required:
```

Delta 只描述两个已验证 Manifest 的差异，不能成为状态源。Manifest 与 Delta 都在 F10 Session Store 的追加式派生表中保存，并用完整性 hash 校验。写入顺序为先 Delta、后 Manifest，只有两者都可用时下一次 Resume 才能发现新的 Manifest；部分状态不会成为可信 Context。

## 6. E6/E7：Summary Cache

新增 `CanonicalSummary`、`CanonicalSummaryCache` 与 `SummarySource`。

Deterministic Summary 支持：

- 结构化 Test Result 的确定性摘要；
- Browser Run/Step 的确定性计数与结果摘要；
- Diff、Artifact 等其他结构化 evidence 的可重建摘要。

摘要保留 `source_refs`、`exact_locator`、`source_hash`、`project_revision`、`policy_hash`、`role_scope`、generator/version、coverage、omitted sections、authority level 和 `summary_hash`。使用前重新校验 locator、revision、policy、role 和 source hash；原文缺失或 hash 不匹配时立即 INVALID。

Semantic Summary 只能以 `SHADOW` 形式保存。`CanonicalSummaryCache` 在 Evaluator 或正式 Context 请求 Semantic Summary 时拒绝，不允许 Generator 语义解释成为 Evaluator 的独立证据。

## 7. E8：Full vs Incremental Equivalence

`compare_mandatory_context()` 比较 Full Safe 与 Incremental 结果的：

- Mandatory Unit 集合；
- Authority；
- Source Hash；
- Exact Locator；
- Role Scope；
- Revision；
- Policy；
- Coverage。

允许缓存状态、invalidation reason 和顺序不同；不允许 Mandatory Unit、Authority、Coverage、Locator、Hash、Role Scope 或 Revision 不同。F14-E 专项覆盖了“不变 Source”和“单个 Code Unit 变化”两类等价性。

## 8. E9：Mutation / Crash / Fault Matrix

已覆盖的变更与故障行为：

| 场景 | 预期 | 结果 |
| --- | --- | --- |
| 无变化 | 最大化 `REUSED_EXACT` | PASS |
| 单个 Code/Test 文件变化 | 只重建受影响 Unit | PASS |
| Requirement/AC/Task 显式依赖变化 | 沿显式边传播失效 | PASS |
| Global Security Constraint 变化 | 相关 Role Context 重新验证 | PASS |
| Approved Plan/Source Hash 变化 | 旧 Unit 不复用 | PASS |
| Revision 变化且内容不变 | 按保守 Revision Policy 重新验证 | PASS |
| Policy Hash 变化 | Cache 失效 | PASS |
| Parser Version 变化 | Parsed Result 失效/重建 | PASS |
| Dependency Graph 变化 | 受影响 Unit 重建 | PASS |
| Index/Manifest/Delta 损坏 | 拒绝可信读取并 Full Safe fallback | PASS |
| Summary Source Hash 变化 | Summary INVALID | PASS |
| Summary Locator 消失 | Summary INVALID | PASS |
| Evaluator 读取 Generator Semantic Cache | 拒绝跨角色/非独立证据复用 | PASS |

崩溃原则是：部分写入、损坏 Manifest、损坏 Delta、损坏 Summary 或无法证明的 Dependency Impact 都不能直接进入正式 Context；允许增加一次 Full Safe Rebuild 成本，不能接受错误复用。

## 9. Telemetry

`RuntimeTelemetry` 新增 `incremental_efficiency`：

- `full_rebuilds`、`incremental_rebuilds`；
- `units_total`、`units_reused`、`units_rebuilt`、`units_invalidated`；
- `bytes_reused`、`bytes_rebuilt`；
- `cache_hits`、`cache_misses`；
- `fallback_full_rebuilds`；
- `summary_hits`、`summary_invalidations`。

同时计算：

```text
Context Reuse Ratio = units_reused / units_total
Incremental Rebuild Ratio = incremental_rebuilds / all rebuilds
Fallback Rate = fallback_full_rebuilds / all rebuilds
```

这些是确定性工作指标，不称为 Token Reduction。真实 token usage 仍只有 Host 提供时才能记录。

F14-E 专项中的“不变 Source”验证确认 `units_reused > 0`；Test Parser 第二次读取复用已验证 parsed cache，parser run 与 file read 均少于第一次 Full 解析路径。

## 10. 正式 Context 与 Invocation 检查

当前正式 Model Context 仍为 F13 Full Safe Context。F14-E 只优化构建、读取、解析、失效、Resume 和派生缓存维护；没有启用 Candidate Selective Context。

当前正式 LLM Invocation 没有减少，也没有引入 Invocation Gate、semantic result reuse、Planner skip、Generator skip 或 Evaluator verdict reuse。F14-F 与 F14-G 均未自动开始。

## 11. 测试证据

| 集合 | 结果 |
| --- | ---: |
| F14-E focused unittest | 13/13 PASS |
| F14-E focused pytest（含 Builder 集成） | 14/14 PASS |
| F14-D focused | 7/7 PASS |
| F14-B focused | 30/30 PASS |
| F14-C focused/bridge/post | 44/44 PASS |
| 原有重点回归 | 120/120 PASS |
| Full unittest discovery | 494/494 PASS |
| `git diff --check` | PASS |

Full suite 最终输出：

```text
Ran 494 tests ...
OK
```

## 12. 最终验收

| F14-E Gate | 结果 |
| --- | --- |
| Critical Mandatory Difference | 0（等价性专项） |
| Authority Difference | 0（等价性专项） |
| Coverage Difference | 0（等价性专项） |
| Stale Cache Accepted | 0 |
| Invalid Summary Used | 0 |
| Evaluator E1 Regression | 0 |
| Existing Regression | 0 |
| Incremental Failure → Full Safe Rebuild | 已实现并由 fallback/故障测试覆盖 |
| Unchanged Source Reuse | `units_reused > 0`，且 Parser/file read 工作量下降 |
| P0/P1 | 未发现 |

F14-E：**PASS**。

本报告完成后停止，等待下一次单独授权。
