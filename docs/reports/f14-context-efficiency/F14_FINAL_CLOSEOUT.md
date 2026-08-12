# F14 Final Closeout

记录日期：2026-08-12
仓库：`C:\Users\28388\Desktop\ai-development-team-skill`
分支：`codex/f10-runtime-correctness-hardening`
基线源提交：`e6131771f13a09ec80da5998c2a4ea9aa1fa6e5d`
基线源 tree：`e9c2ac7557f43a45eddade341e1b9067d813ec52`

## 最终结论

工程治理已经收口，真实 Gold 环境仍不可用。因此本次收尾的最终状态是：

> **F14 ENGINEERING COMPLETE / CONTROLLED QUALIFIED**
> **GLOBAL BLOCKED ONLY BY EXTERNAL REAL-GOLD ENVIRONMENT**

本报告记录最终提交前的基线与验证结果；最终 commit/tree SHA 在提交完成后的冻结结果中报告，不回写到包含本报告的同一 commit，因此不会形成自引用循环。没有创建 PR，也没有修改历史 Qualification 报告或强行开启 Global。

## 1. Baseline

Phase 0 基线保存在 [F14_FINAL_CLOSEOUT_BASELINE.md](F14_FINAL_CLOSEOUT_BASELINE.md)。开始修改前已记录 commit、branch、工作区状态、HEAD tree、Authority 文件 SHA-256 与测试基线。

开始修改前的可复现结果：

- Protocol Checker：PASS，8 checks。
- focused governance unittest：12/12 PASS。
- 完整 unittest：509/509 PASS。
- 完整 pytest：本轮首次执行受临时目录/时间限制影响，附件给出的既有基线为 930 passed、6 skipped、129 subtests passed。
- `git diff --check`：PASS。

## 2. Governance fixes

- `scripts/protocol_consistency.py` 的业务检查函数现在显式接收 `root: Path`。
- schema、template、`runtime/role_selector.py` 和 `config/retry_governance.yaml` 均从传入 root 读取，不再通过 module-level `ROOT` 偷读真实仓库。
- `run_check(root)` 将 root 传入 `load_documents`、`load_document_texts`、`check_documents` 及所有检查函数；默认 CLI 仍指向真实仓库。
- F14 Feature Qualification 改为能力级 Gate：Selective、Invocation Gate、Evaluator Selective 分别验证自己的证据；Global 仍要求完整证据链。
- F13 `f13_full`、自动回退、Kill Switch 和 `FALLBACK_F13` 保留。

## 3. Root-isolation evidence

新增 clone fault-injection 测试，均 PASS：

- clone 的 schema/template 损坏，真实仓库保持正常：Checker FAIL。
- clone 的 role selector wait logic 损坏：Checker FAIL。
- clone 的 retry governance 损坏：Checker FAIL。

这证明 Checker 使用传入仓库作为唯一 Authority，而不是混合读取真实仓库。

## 4. Feature-specific qualification gates

运行时与 Checker 均 fail closed：

- Selective Context：要求 `controlled=PASS`，且资格状态不能是 `IMPLEMENTED` 或 `FALLBACK_F13`。
- Invocation Gate：要求 `controlled=PASS`、`fault_injection=PASS`，且 `semantic_task_misclassified_as_python_only=0`。
- Evaluator Selective：要求 `evaluator_selective=PASS`、`quality_parity=PASS`、`real_model=PASS`、`real_browser=PASS`。
- Global：继续要求 `controlled`、`real_model`、`real_browser`、`evaluator_selective`、`quality_parity`、`fault_injection`、`fallback` 全部为 PASS，并同时满足 `mode=global`、`qualification_status=GLOBAL_ENABLED`、`global_enabled=true`。

新增的 Selective deny、Invocation deny、Evaluator Selective deny 和 Global bypass 测试均通过。当前正式配置中的 F14 开关仍全部关闭。

## 5. Research Gate semantic correction

当前正式语义已统一为：

> Research Gate 不能被跳过；Research Execution 可以由确定性 Research Gate 判定为 `required`、`optional` 或 `not_required`。

所有项目都必须经过 Research Necessity Decision，但不是所有项目都必须联网 Research。已同步到 Skill、First-Ask、Planner、工作流协议和 Planner 审批文档。

## 6. Protocol Manifest route correction

`config/protocol_manifest.yaml` 不再使用会误导为严格线性流程的 `research_sequence`，改为 `route_contract.requirements_discovery`，明确：

- `research_gate_required: true`
- `research_execution_decisions: [required, optional, not_required]`
- `research_entry_state: REQUIREMENT_RESEARCH`
- `research_return_state: INTAKE`
- `reference_analysis_optional: true`
- `reference_analysis_state: REFERENCE_ANALYSIS`
- `planning_target: PLANNING`

Checker 已同步校验该真实 Route Contract。

## 7. Full regression result

最终文件状态下已执行：

| 验证 | 结果 |
| --- | --- |
| `python -B scripts/protocol_consistency.py check` | PASS，8 checks |
| `python -B -m unittest tests.test_protocol_consistency tests.test_f14_rollout_governance -v` | PASS，22/22 |
| `python -B -m unittest discover -s tests -p "test*.py"` | PASS，519/519 |
| `python -B -m pytest -q -p no:cacheprovider` | PASS，940 passed、6 skipped、129 subtests passed |
| `git diff --check` | PASS |

## 8. Real Host capability result

Real Model Qualification 没有启动伪造请求：当前线程没有可用于真实模型 invocation 的已认证 Host channel，也没有实际 invocation metadata。以下字段均为 `UNAVAILABLE_FROM_HOST`，不能用 fixture、bytes、字符数或 tokenizer 估算替代：

- model identity
- invocation id
- input tokens
- cached input tokens
- output tokens
- total tokens
- request count
- latency

结论：**Real Model = BLOCKED**。

## 9. Real Browser capability result

已通过 Codex In-app Browser 进行只读检查：`openTabs=[]`、`activeTabs=[]`。当前仓库是 Skill 本体，不是 managed project；在不读取其他项目数据的前提下，没有可运行的真实 Case C Gold Project、Generator 生成的 Product target 或 Browser URL。

结论：**Real Browser = BLOCKED**。没有创建替代 demo HTML，也没有把 controlled fixture 冒充 Browser Gold。

## 10. Real Gold result

Real Gold Qualification 未启动。因为 Real Model 与真实 Case C Browser target 两个前置条件都不满足，不能声称 Case C 生命周期、浏览器场景、真实 token telemetry 或 Real Gold correctness PASS。

Production Correctness 因缺少真实 Gold 的 required scenarios/evidence 保持 BLOCKED；Efficiency 因没有真实 Host token telemetry 保持 BLOCKED，未宣称任何真实 token saving。历史 controlled fixture bytes 只保留为受控工程证据，不升级为真实 token 结论。

## 11. Evaluator Selective result

Evaluator Selective 不会因 Planner/Generator 的受控资格自动开启。当前 `evaluator_selective`、`quality_parity`、Real Model 和 Real Browser 的正式证据不足，因此：

- Evaluator Selective：BLOCKED
- `evaluator_selective_context.enabled`：false
- Evaluator false PASS / detection parity：没有真实 Gold 证据，不作生产结论

## 12. F13 fallback

F13 Emergency Safe Mode：**READY**。

- 默认 Context Delivery：`f13_full`。
- 自动回退：开启且固定为 `f13_full`。
- Kill Switch 与 `rollback()`：回到 `FALLBACK_F13`。
- F13 回归测试：通过。

## 13. Final rollout state

当前正式配置仍为受控资格，不是生产 Rollout：

```yaml
f14:
  context_delivery_mode: f13_full
  selective_context:
    enabled: false
  evaluator_selective_context:
    enabled: false
  invocation_gate:
    enabled: false
  rollout:
    mode: controlled
    qualification_status: CONTROLLED_QUALIFIED
    fallback_mode: f13_full
    automatic_fallback: true
    global_enabled: false
  qualification_evidence:
    controlled: PASS
    real_model: BLOCKED
    real_browser: BLOCKED
    evaluator_selective: BLOCKED
    quality_parity: BLOCKED
    semantic_task_misclassified_as_python_only: 0
    fault_injection: PASS
    fallback: PASS
```

## 14. Remaining blockers

- `BLOCKER-F14-RM-001: REAL_CODEX_HOST_UNAVAILABLE`：没有已认证的真实 Codex Host invocation channel 和 usage metadata。
- `BLOCKER-F14-RB-001: REAL_CASE_C_BROWSER_TARGET_UNAVAILABLE`：没有可运行的真实 Case C managed project 与 Generator 生成的 Browser target。
- `BLOCKER-F14-GLOBAL-001: REAL_GOLD_QUALIFICATION_ENVIRONMENT_UNAVAILABLE`：上述两个外部 Real Gold 条件同时缺失的聚合 blocker。

这些 blocker 是外部 Real Gold 环境缺口，不是继续扩展 F14 架构的理由。

## Frozen Controlled Qualification Baseline

- Baseline name：`F14_CONTROLLED_QUALIFIED_BASELINE`
- Branch：`codex/f10-runtime-correctness-hardening`
- `baseline_source_commit`：`e6131771f13a09ec80da5998c2a4ea9aa1fa6e5d`
- `baseline_source_tree`：`e9c2ac7557f43a45eddade341e1b9067d813ec52`
- Protocol version：`7`
- Project schema version：`7`
- F14 qualification status：`CONTROLLED_QUALIFIED`
- Context delivery mode：`f13_full`
- Global：`NOT_ENABLED`
- F13 fallback：`READY`
- Final regression：Protocol Checker 8 checks PASS；focused Governance 22/22 PASS；full unittest 519/519 PASS；full pytest 940 passed、6 skipped、129 subtests passed；`git diff --check` PASS。
- `final_commit`：提交完成后在最终冻结结果中报告，不回写本 commit。
- `final_tree`：提交完成后在最终冻结结果中报告，不回写本 commit。
- `frozen_baseline_tag_or_external_reference`：`pending`

## 15. Exact final config

最终 Authority 是 `config/f14.yaml` 与 `config/protocol_manifest.yaml`。配置摘要见本报告第 6、13 节；对应完整机器结果见 [F14_FINAL_CLOSEOUT_RESULTS.json](F14_FINAL_CLOSEOUT_RESULTS.json)。Global 没有手工跳级，F13 fallback 没有关闭。

## 16. Modified files

本轮收尾触及的文件：

- `SKILL.md`
- `config/f14.yaml`
- `config/protocol_manifest.yaml`
- `config/requirements_discovery.yaml`
- `docs/PLANNER_APPROVAL_WORKFLOW.md`
- `docs/RUNTIME_CAPABILITY_STATUS.md`
- `docs/workflow_protocol.md`
- `intake/first_ask.md`
- `prompts/planner_prompt.md`
- `runtime/f14_control.py`
- `runtime/invocation_gate.py`
- `scripts/protocol_consistency.py`
- `tests/test_f14_f_context_and_invocation.py`
- `tests/test_f14_f_preflight.py`
- `tests/test_f14_rollout_governance.py`
- `tests/test_protocol_consistency.py`
- `docs/reports/f14-context-efficiency/F14_FINAL_CLOSEOUT_BASELINE.md`
- `docs/reports/f14-context-efficiency/F14_FINAL_CLOSEOUT.md`
- `docs/reports/f14-context-efficiency/F14_FINAL_CLOSEOUT_RESULTS.json`

历史报告未重写；工作区既有的其他用户修改保持原样。

## 17. Unresolved risks

- 没有真实 Host usage metadata，不能估算或声称 token 节省。
- 没有真实 Case C Browser target，无法验证 app load、桌面/移动布局、导航、主流程、持久化、刷新、错误态和视觉验收场景。
- Evaluator Selective 的真实 parity 尚未执行，因此不能进入 Global。
- 当前受控配置安全地保持 `f13_full`；后续若提供真实环境，必须从同一 frozen checkpoint 执行 Real Model、Real Browser、Evaluator Selective 和 F13/F14 A/B 资格链，不得手工改 YAML 跳级。

## Fixed conclusion format

Protocol Governance: **PASS**
Protocol Drift: **PASS**
F14 Engineering: **PASS**
Controlled Qualification: **PASS**
Real Model: **BLOCKED**
Real Browser: **BLOCKED**
Production Correctness: **BLOCKED**
Efficiency: **BLOCKED**
Evaluator Selective: **BLOCKED**
F13 Emergency Fallback: **READY**
Global Rollout: **NOT_ENABLED**
Remaining Blockers: **BLOCKER-F14-RM-001, BLOCKER-F14-RB-001, BLOCKER-F14-GLOBAL-001**
