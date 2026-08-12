# F14-C Post-Verification Report

报告日期：2026-08-12
验证范围：F14-C Context Architecture C0–C6 的 Post-Verification
架构变更：无
最终结论：**技术 PASS；F14-D 可 GO，但本次不自动启动**

## 1. 验证结论

本轮只验证 F14-C，不修改 Context 优化架构，不改变 F13 正式 Context 交付，不改变模型调用路径，也没有进入 F14-D。

- F14-C 原始 focused pytest：**21/21 PASS**。
- F14-C unittest discovery bridge：**21/21 PASS**。
- 合法项目 C4 smoke：**12/12 ALLOW**，False Block Rate **0%**。
- Coverage unavailable / authoritative incomplete 语义测试：**PASS**。
- 原有重点回归：**120/120 PASS**。
- 最终完整 `unittest discover`：**474/474 PASS**。
- `git diff --check`：**PASS**。

F14-D 判定为 **GO（门槛结论）**，仅表示当前证据满足进入下一阶段的技术前置条件；当前执行状态仍为 **STOP / NOT STARTED**，没有自动进入 F14-D。

## 2. 为什么此前 21 个 focused tests 没有让 451 增加

原因已经复现并确认：21 个原始测试都是 pytest 风格的模块级函数，函数位于下列 8 个文件中；它们没有继承 `unittest.TestCase`，因此不会被 `python -m unittest discover` 收集。

修复前的直接证据：

```text
python -m unittest discover -s tests -p 'test_f14_c*.py' -v
Ran 0 tests in 0.000s
OK
```

因此，原来的 451 是 unittest 基线，并不包含这 21 个 pytest focused 函数。此前 focused 命令单独运行的是 pytest 收集结果，两套测试发现机制不同。

本轮新增 `tests/test_f14_c_unittest_bridge.py`：每个原始 focused 函数都有一个同名的一对一 `unittest.TestCase` wrapper；需要 `tmp_path` 的测试由 bridge 提供等价临时目录。bridge 不复制测试逻辑，只把同一测试函数接入 full discovery。

修复后的证据：

```text
python -m unittest discover -s tests -p 'test_f14_c*.py' -v
Ran 23 tests ...
OK
```

这 23 项由 21 个 bridge 测试和 2 个 Post-Verification 测试组成。最终总数为：

```text
451 原 unittest 基线
+ 21 F14-C bridge tests
+ 2 F14-C Post-Verification tests
= 474 full discovery tests
```

## 3. 21 个 focused tests 的完整清单

原始 pytest 测试和 full discovery bridge 的 test name 一一对应：

### `tests/test_f14_c_preflight.py`

- `test_c0_control_plane_path_is_outside_project_workspace`
- `test_c0_authority_cannot_be_spoofed_by_locator_or_revision`
- `test_c0_real_model_boundary_counts_adapter_failure_as_llm`

### `tests/test_f14_c1_semantic_model.py`

- `test_c1_semantic_model_is_role_task_revision_scoped_without_delivery_change`

### `tests/test_f14_c_shadow_coverage.py`

- `test_global_constraint_without_direct_graph_edge_is_still_mandatory`
- `test_shadow_candidate_can_show_reduction_only_when_mandatory_is_complete`
- `test_stale_and_hash_conflict_never_pass_coverage`

### `tests/test_f14_c4_enforced_gate.py`

- `test_c4_incomplete_mandatory_coverage_blocks_before_model_call`
- `test_c4_pass_keeps_f13_full_context_and_does_not_deliver_candidate`
- `test_c4_optimizer_crash_falls_back_to_existing_f13_context`

### `tests/test_f14_c5_selective_candidate.py`

- `test_c5_eligible_candidate_is_only_an_evaluation_artifact`
- `test_c5_unknown_authority_forces_safe_fallback`

### `tests/test_f14_c_storage.py`

- `test_c1_c5_derived_records_are_append_only_and_integrity_checked`

### `tests/test_f14_c_shadow_first_runtime.py`

- `test_shadow_first_runtime_persists_evidence_without_replacing_f13_package`

### `tests/test_f14_c6_fault_injection.py`

- `test_case_missing_acceptance_criterion_is_not_pass`
- `test_case_unknown_edge_connected_to_mandatory_is_unknown_impact`
- `test_case_requirement_plan_conflict_is_not_pass`
- `test_case_stale_approved_scope_is_fail_closed`
- `test_case_optimizer_unavailable_falls_back_without_empty_context`
- `test_case_role_scope_is_not_cross_role_coverage`
- `test_case_candidate_missing_security_constraint_never_passes`

原始 pytest 直接运行结果：

```text
python -m pytest -q tests/test_f14_c_preflight.py tests/test_f14_c1_semantic_model.py \
  tests/test_f14_c_shadow_coverage.py tests/test_f14_c4_enforced_gate.py \
  tests/test_f14_c5_selective_candidate.py tests/test_f14_c_storage.py \
  tests/test_f14_c_shadow_first_runtime.py tests/test_f14_c6_fault_injection.py
21 passed in 4.17s
```

## 4. C4 合法项目状态 smoke test

验证入口使用真实的测试 Runtime 项目状态：项目根目录有 `project.yaml`，Session Store 位于项目外部 Control Plane，使用正式 `ContextBuilder` 生成 F13 Context，再将同一合法状态送入 C4 Enforced Coverage Gate。

为避免把角色路径策略误判成 Coverage False Block，四类画像使用各角色实际允许的来源目录：

| 项目画像 | 语义覆盖 | Planner 来源 | Generator 来源 | Evaluator 来源 |
|---|---|---|---|---|
| simple project | 单文件基础项目 | `memory/requirements/` | `code/` | `evaluation/evidence/` |
| bookkeeping App | ledger、invoice、reconciliation | 同左 | 同左 | 同左 |
| cross-file project | domain、service、API 跨文件依赖 | 同左 | 同左 | 同左 |
| high-complexity business | tenancy、ledger、payment、compliance、workflow、settlement | 同左 | 同左 | 同左 |

每种画像分别执行 Planner、Generator、Evaluator，共 12 个合法状态。每个状态均满足：

- mandatory closure：7/7 covered；
- C4 decision：`ALLOW`；
- delivery mode：`F13_FULL`；
- 模型收到的对象仍是当前 F13 Full Context；
- candidate 没有成为正式模型输入。

结果矩阵：

| 项目画像 | Planner | Generator | Evaluator |
|---|---|---|---|
| simple project | ALLOW / 7/7 | ALLOW / 7/7 | ALLOW / 7/7 |
| bookkeeping App | ALLOW / 7/7 | ALLOW / 7/7 | ALLOW / 7/7 |
| cross-file project | ALLOW / 7/7 | ALLOW / 7/7 | ALLOW / 7/7 |
| high-complexity business | ALLOW / 7/7 | ALLOW / 7/7 | ALLOW / 7/7 |

合法项目错误率：

```text
legal_cases                 = 12
False Block                 = 0
False Block Rate            = 0/12 = 0%
False Omission              = 0
False Mandatory             = 0
Critical False Omission     = 0
```

## 5. unavailable 与 authoritative incomplete 的语义区分

两者不是同一种失败：

| 情况 | 证据含义 | C4 行为 | 是否调用模型 |
|---|---|---|---|
| Coverage computation unavailable | Artifact Index、Dependency Graph、Shadow Classifier 或 Candidate Optimizer 自身不可用，Runtime 没有权威 Coverage 结论 | `FALLBACK_F13`，保留当前 F13 Full Safe Context | 是，使用 F13 |
| Authoritative coverage genuinely incomplete | Runtime 已形成权威证据，明确发现 `MISSING`、`STALE`、`CONFLICT` 或 `UNKNOWN` mandatory coverage | `BLOCK` / `CONTEXT_COVERAGE_BLOCKED` | 否 |

本轮对四类计算组件分别做了 unavailable 注入：`ArtifactIndex`、`DependencyGraph`、`ShadowClassifier`、`CandidateOptimizer`。四类均验证为 `FALLBACK_F13`，且模型仍收到原 F13 Context，不会收到空 Context。

随后构造权威 incomplete 状态，删除 mandatory `SECURITY-001`，由 Shadow Classification 和 Comparison 产生 `mandatory_missing`。此时 Coverage Gate 返回 `FAIL`，C4 阻断模型调用；这不是 fallback，因为 Runtime 已经知道候选覆盖确实不完整。

已有故障注入同时覆盖：

- stale approved scope：`STALE`，fail-closed；
- requirement/plan hash mismatch：`CONFLICT`，fail-closed；
- unknown edge 连接 mandatory 闭包：`UNKNOWN`，不能当作无影响；
- 缺失 acceptance criterion / security constraint：`MISSING`，不能放行。

因此只有 Runtime 的权威 Coverage 证据可以触发 BLOCK；索引或优化器“算不出来”只能回退 F13。

## 6. 错误率定义与记录

- **False Omission Rate**：本来应该进入 Mandatory Context，却被遗漏。本轮合法矩阵中为 0；受控 mandatory 缺失均被检测，没有观察到误放行。Critical False Omission 为 **0**。
- **False Mandatory Rate**：本来不应该 Mandatory，却被错误强制加入。本轮 12 个合法状态中的 task-relevant 文件均未进入 mandatory closure，记录为 **0/12**。该字段作为后续 Context Efficiency 优化的基线：优化只能优先处理 optional/task-relevant 候选，不得将其无证据升级为 mandatory。
- **False Block Rate**：合法可执行项目被 Coverage Gate 错误阻止。本轮 12 个合法状态全部 ALLOW，记录为 **0/12 = 0%**。

本轮数值来自受控合法 fixture，不宣称真实生产语料的统计精度；后续效率优化仍需持续记录这三个指标，并单独监控 Critical False Omission。

## 7. 完整测试证据

| 验证集合 | 结果 |
|---|---:|
| F14-C 原始 focused pytest | 21/21 |
| F14-C unittest bridge | 21/21 |
| C4 合法项目 smoke | 12/12 |
| unavailable / authoritative incomplete | PASS |
| F14-C discovery（bridge + post） | 23/23 |
| 原有重点回归 | 120/120 |
| 完整 `python -m unittest discover -s tests -p 'test*.py'` | 474/474 |
| F14-B focused 历史证据 | 30/30 |

最终完整 suite 输出：

```text
Ran 474 tests in 38.229s
OK
```

## 8. F14-D 决策与停止条件

F14-D：**GO（技术门槛通过）**。

但本轮执行明确停止在 F14-C Post-Verification：

- 不自动进入 F14-D；
- 不开放 Model Arbitrary Context Request；
- 不把 candidate Context 设为正式默认输入；
- 不修改 Context 优化架构；
- 等待用户单独授权后，才能启动 F14-D。
