# F14-B Deterministic Extraction 报告

阶段：F14-B — Deterministic Extraction  
完成日期：2026-08-11  
最终结论：PASS  
范围：只实现 B1–B8；未进入 F14-C，未改变正式 Context 选择和 Planner/Generator/Evaluator Invocation 数量。

## 1. 本阶段结果

本阶段建立了可重建的确定性基础设施：

- Minimal Telemetry。
- F10 Session Store 派生 Artifact Index。
- Authority/Provenance 与 Execution/Verification 双语义 Dependency Graph。
- approved baseline 到 current revision 的 Diff Index。
- 两层 Source Fingerprint/Content-Addressed Cache。
- 结构化优先的 Test Output Parser。
- crash、corruption、路径、Secret、revision、policy、parser 和 unknown 负向测试。

本阶段没有实现：

- Complete Mandatory Requirement Coverage。
- Context Coverage Gate。
- Context Escalation。
- Selective Context 或 Context 缩减。
- Invocation Gate。
- LLM Semantic Result Reuse。
- Project Effort Profile。

## 2. 新增文件

### Runtime

- runtime/deterministic/__init__.py
- runtime/deterministic/telemetry.py
- runtime/deterministic/store.py
- runtime/deterministic/artifact_index.py
- runtime/deterministic/dependency_graph.py
- runtime/deterministic/diff_index.py
- runtime/deterministic/source_cache.py
- runtime/deterministic/test_parser.py

### Tests

- tests/test_f14_deterministic_extraction.py
- tests/test_f14_artifact_index.py
- tests/test_f14_dependency_graph.py
- tests/test_f14_diff_index.py
- tests/test_f14_source_cache.py
- tests/test_f14_test_parser.py
- tests/test_f14_b7_safety.py

### Report

- docs/reports/f14-context-efficiency/F14_B_DETERMINISTIC_EXTRACTION_REPORT.md

## 3. 修改文件

- runtime/session_store.py
  - 内部 Runtime schema 从 4 追加到 10。
  - 通过幂等 DDL 增加 F14 派生表。
  - 不修改 project.yaml、业务状态、Lease、CAS、Session lifecycle 或历史业务表语义。
- runtime/phase_runner.py
  - 增加 best-effort Telemetry 记录。
  - 只记录 Context 数量/字节和真实模型调用入口，不改变调用流程、Context 内容或正式 Invocation 数量。
- 既有 F14-A 文档没有被覆盖；本阶段只新增本报告。

## 4. Runtime Schema 变化

F14 派生数据均位于现有 F10 Session Store / Runtime Control Plane 中：

| 表 | 用途 |
| --- | --- |
| f14_telemetry | 追加式 Runtime/Context/Model 安全计数 |
| f14_artifact_index_snapshots | Artifact Index 完整快照和 integrity hash |
| f14_artifact_records | 快照内的 Artifact 记录 |
| f14_dependency_graph_snapshots | Dependency Graph 快照和 integrity hash |
| f14_dependency_edges | Authority/Execution 显式 Edge |
| f14_diff_indexes | Diff 结构和 integrity hash |
| f14_source_fingerprints | Layer 1 locator/metadata fingerprint |
| f14_content_cache | Layer 2 trusted content hash 到 bytes |
| f14_parsed_cache | content hash + parser/policy/role 绑定的解析结果 |
| f14_test_results | Parser 输出、状态、locator 和 integrity hash |

这些表均由 SessionStore 初始化时幂等创建。事务中断后，未提交记录不会成为可见快照；已存在的派生表可以由同一初始化路径补齐。派生数据损坏只会触发 corruption、fallback、rebuild、stale/unknown 或阻塞，不会修改项目业务状态。

## 5. Minimal Telemetry

### 数据域

~~~yaml
runtime_efficiency:
  file_reads:
  bytes_read:
  hash_operations:
  directory_scans:
  parser_runs:
  runtime_latency:

context_efficiency:
  context_source_count:
  context_bytes:

model_efficiency:
  real_llm_invocations:
  input_tokens:
  cached_input_tokens:
  output_tokens:
  model_latency:
~~~

F14-B 未伪造 mandatory_bytes、task_relevant_bytes 或 on_demand_bytes。Token usage 没有 Host 真实数据时保持 unavailable，绝不以 bytes 冒充 token。

### execution_type

支持：

- python_only
- llm
- perception
- reused_deterministic
- rollover

Invocation lifecycle record 数量与 Real LLM Invocation Count 分开。Change Request 的确定性 Runtime record 不会因为统计优化被删除。

Telemetry 不保存完整模型输出、原始长日志、Secret 或凭据；包含 schema version、幂等 key 和受限标签。Telemetry 持久化失败不会阻断业务流程。

## 6. Artifact Index 结构

每条 ArtifactRecord 至少包含：

~~~yaml
artifact_id:
kind:
locator:
content_hash:
project_revision:
policy_hash:
producer_role:
authority:
approval_status:
freshness:
source_state_ref:
~~~

authority 白名单：

- USER_EXPLICIT
- APPROVED_REQUIREMENT
- APPROVED_PRODUCT_SPEC
- APPROVED_PLAN
- APPROVED_CHANGE_SCOPE
- RUNTIME_EVIDENCE
- GENERATED_ARTIFACT
- HISTORICAL
- UNKNOWN

Artifact Index 只接受调用方提供的显式 locator，不根据文件名、关键词或内容猜业务状态。快照按 artifact_id 排序并保存整体 integrity hash；删除或损坏后可从原始来源重新构建。

## 7. Dependency Graph Edge Model

逻辑上分为两张图：

### Authority / Provenance Graph

允许 derived_from、approved_by、supersedes、sourced_from、governed_by。

### Execution / Verification Graph

允许 satisfies、implements、affects、verifies、evidenced_by、regresses_with。

每条边包含：

~~~yaml
graph_kind:
edge_type:
source:
target:
evidence_ref:
source_hash:
revision:
confidence:
~~~

confidence 只有 explicit 和 unknown。explicit 必须有明确端点、authority/source、exact evidence locator、可信 source hash 和当前 revision。unknown 不进入 explicit_edges，不会被转换为 not_affected。

## 8. Diff Index 结构

DiffIndex 输出：

~~~yaml
baseline_revision:
current_revision:
changed_files:
changed_artifacts:
changed_hashes:
explicitly_affected_nodes:
unknown_impact_nodes:
stale:
~~~

Diff 只比较受控 baseline/current snapshot 和显式影响节点。Baseline 缺失、current revision 不匹配、路径逃逸会 fail closed。文件名相似、关键词和模型猜测不能产生明确 AC 影响。

## 9. Source Cache 结构

### Layer 1 — Source Fingerprint / Locator Cache

记录 canonical locator、file identity、size、mtime、filesystem metadata、project revision、policy hash、role scope、parser version 和 known content hash。它只用于快速判断是否需要重新读取，不是 authority。

### Layer 2 — Content Addressed Cache

结构为：

    trusted_content_hash
      -> decoded content
      -> parsed representation
      -> deterministic derived result

decoded content 按 trusted content hash 保存；parsed/derived result 额外绑定 parser version、policy hash 和 role scope。F14-B 不缓存 Planner semantic output、Generator implementation output 或 Evaluator verdict。

fingerprint 快命中返回 trusted=false；默认读取、protected source、approved source 和 security-sensitive source 都强制重新读取并计算可信 hash。缓存命中仍执行 path authorization、Secret policy 和 freshness 检查。

## 10. Cache Invalidation Rules

以下情况会失效或触发 fallback：

1. locator 变化。
2. file identity 变化。
3. workspace/path authorization 变化。
4. size、mtime 或 filesystem metadata 变化。
5. protected、approved 或 security-sensitive source。
6. project revision 不匹配。
7. policy hash 不匹配。
8. role scope 不匹配。
9. parser version 不匹配。
10. trusted content hash 变化。
11. source hash 与 known content hash 不一致。
12. schema/checksum/content hash/parsed hash corruption。
13. locator 不可读、文件缺失或符号链接/重解析路径异常。
14. Cache hit 不能跳过 Secret、path 和 freshness 规则。

Cache event 至少包含 hit_or_miss、invalidation_reason、fallback_read 和 parser_version。fingerprint 或 content cache 写入失败不会让源读取失败；后续读取可以重新构建缓存。

## 11. Test Output Parser 结构

输入优先级固定为：

1. Existing Structured Runtime Result。
2. JUnit / machine-readable result。
3. Structured pytest-compatible result，包括 test_metrics。
4. Human console fallback。

输出包含：

~~~yaml
parse_status:
parser_version:
command:
exit_code:
duration_ms:
tests:
  total:
  passed:
  failed:
  skipped:
  error:
failed_tests:
coverage_metrics:
source_locator:
raw_log_locator:
confidence:
~~~

结构化结果优先于 console；JUnit malformed、console ambiguous、command/raw locator 缺失时返回 parse_status: UNKNOWN，不能自动返回 passed: true。原始 evidence 只保存 locator，不删除原文。

## 12. Crash Recovery

已验证：

- Artifact Index 写入事务失败后没有留下 partial snapshot。
- Artifact/Graph/Diff/Parser 快照 integrity mismatch 会阻断读取。
- Content cache checksum/hash 损坏时新实例会忽略缓存并回读源文件。
- Fingerprint metadata JSON 损坏时会记录 corruption 并回读。
- Cache write 失败不阻断可信源读取。
- Parser 失败不删除 raw evidence locator。
- Session Store 初始化和现有 v7 migration/recovery 仍通过。
- 同一快照/Telemetry 幂等 key 不重复生成派生记录。

## 13. 负向测试

F14-B 新增 focused tests 共 30 个，覆盖：

- Artifact 删除、mutation、非法 authority、路径越界、索引 corruption。
- Authority/Execution 非法 edge、缺 evidence、unknown dependency、Graph corruption。
- Diff baseline 缺失、current revision mismatch、路径逃逸、Diff corruption。
- Source Cache metadata/revision/policy/role/parser 失效。
- content checksum/hash corruption、fingerprint corruption、cache write crash。
- Secret policy、symlink/reparse/path anomaly、业务状态不变。
- Parser malformed JUnit、ambiguous console、UNKNOWN、raw locator 缺失、结果 corruption。
- Telemetry failure non-fatal、Invocation record 与真实 LLM count 分离。

## 14. 测试结果

测试进程使用 Skill 仓库外目录：

    C:\Users\28388\Desktop\f14-a-temp-tests-20260811

并显式设置 TEMP、TMP、TMPDIR。

| 集合 | 结果 | 状态 |
| --- | --- | --- |
| F14-B focused | 30/30，0 failure，0 error，2.211 秒 | Current Run Verified |
| 原 F14-A 重点集合 | 120/120，0 failure，0 error，1.183 秒 | Current Run Verified |
| 完整 tests 集合 | 451/451，0 failure，0 error，27.714 秒 | Current Run Verified |

历史 baseline 为 421/421；新增 F14-B focused tests 为 30 个。本阶段没有删除测试、放宽断言、修改验收阈值或跳过失败测试。

## 15. 效率证据

### Runtime Efficiency

F14-A 之前没有可复现的 Runtime file/hash/parser telemetry，因此生产工作流的历史数值标记为 unavailable。

确定性微基准对同一 20-byte 文件执行两次读取：

| 指标 | 无缓存重复读取基线 | F14-B fingerprint 命中 |
| --- | ---: | ---: |
| file_reads | 2 | 1 |
| bytes_read | 40 | 20 |
| hash_operations | 2 | 1 |
| cache hit | 0 | 1 |

这是确定性 Cache 微基准，不代表完整生产工作流的 latency 提升，也不代表 Token 减少。

### Context Efficiency

F14-B 没有改变 Context Builder 的来源集合、选择行为、预算或交付内容。Mandatory/Task Relevant/On Demand 仍未启用，Context bytes 和 source count 只做观测准备，不宣称 Context 优化。

### Model Efficiency

F14-B 没有减少正式 Planner/Generator/Evaluator Invocation，没有跨角色复用，也没有 semantic result cache。F14-B 测试不调用真实 LLM，real_llm_invocations 为 0；input_tokens、cached_input_tokens、output_tokens 和 model_latency 为 unavailable。正式 PhaseRunner 仅增加调用入口观测。

## 16. P0/P1 问题

本阶段测试和安全自审未发现 P0/P1 问题。该结论仅针对本阶段实现和当前测试证据，不替代后续 F14-C 的 Coverage/Selective Context 质量评审。

## 17. 十项范围自审

| 问题 | 结果 |
| --- | --- |
| 是否修改 Model Invocation 数量 | 否 |
| 是否改变 Model Context | 否 |
| 是否跨角色复用语义结果 | 否 |
| 是否产生第二状态源 | 否，全部位于 F10 Session Store 派生区 |
| 是否绕过 path/security policy | 否，Cache hit 仍执行检查 |
| 是否可能使用 stale cache | 默认 trusted read fail closed；fingerprint 命中明确标记 trusted=false |
| unknown 是否被解释为 no impact | 否 |
| 是否有权威原件无法恢复 | 否，Index/Graph/Cache 均保存 locator/hash |
| 是否破坏 E1 | 否，E1/role isolation 回归通过 |
| 是否修改 approval semantics | 否 |

## 18. F14-B 最终结论

**PASS**

PASS 的依据是：

- B1–B6 已实现，B7 负向/恢复测试完成。
- F14-B focused 30/30 通过。
- 原 120 个重点测试 120/120 通过。
- 完整测试 451/451 通过。
- 没有发现 Existing Product Regression、F14 P0/P1、Context/Invocation 行为变化或安全边界退化。

本报告完成后停止。不得自动进入 F14-C，不得开始 Mandatory Context 分类、Selective Context、Coverage Gate、Context Escalation 或 Context 缩减，等待用户下一次明确授权。
