# R6 Evaluator Reference Conformance Report

- Date: 2026-08-09
- Scope: Phase R6 only
- Previous baseline: R0 PASS, R1 PASS, R2 PASS, R3 PASS, R4 PASS, R5 PASS READY_WITH_LIMITATIONS

## R6 RESULT

PASS — READY_WITH_LIMITATIONS。

R6 已完成批准 Reference Requirement → R5 Approved Reference Contract → Generator Handoff /
Implementation Evidence → Evaluator Evidence Manifest → Reference Conformance Gate → Issue /
Failure Routing 的闭环。没有创建第四个 Agent、平行 Evaluation 流程或新的工作流状态。

## Reference Conformance Architecture

- `scripts/reference_conformance.py` 是纯确定性校验模块，消费已批准 Contract、批准 Product Spec/Plan、Evaluator 注册的独立证据和 Runtime 能力矩阵。
- Gate 使用现有 `evidence_manifest` 生命周期，Gate ID 为 `GATE-REFERENCE-CONFORMANCE`，没有新增模型步骤。
- 无批准 Contract 或 Contract 不含绑定时，结果为 `NOT_APPLICABLE`，不创建失败 Issue。
- 有绑定时每个 `REFDEC-*` 独立核对，整体结果按 `PASS`、`FAIL`、`BLOCKED`、`UNVERIFIED` 汇总。
- Evaluator 不读取 URL、HTML、截图原件或原始 Reference Archive；R5 Contract 的 `raw_reference_access: false` 和 `untrusted_reference_data` 约束继续生效。

## Gate Integration

- `GATE-REFERENCE-CONFORMANCE` 已加入稳定 Gate 顺序，位于 Feature Completeness 与 Regression 之间。
- Gate 在 `evidence_manifest` Runtime Verifier 中执行；缺少绑定时确定性返回 N/A，不能被模型声明覆盖。
- 既有 Browser Scenario、Feature Completeness、Build/Test/Regression、Evidence Manifest、Issue Package、Candidate 和 Evaluation Transaction 步骤保持不变。

## Approved Contract Consumption

- Evaluator Context 新增受控 `reference_conformance_subset`，只内联批准 Contract 摘要及其 hash。
- Evaluator 同时读取批准 Product Spec、批准 Plan、`latest_handoff`、Generator Response 和 Evidence Manifest 指针。
- Contract ID、Contract hash、REFDEC 集合、AC refs、Plan refs 不匹配时 fail closed。

## Binding Verification

- 每个绑定必须精确回指 `acceptance_refs` 与 `plan_refs`，并且结果集合不得缺少或增加绑定。
- Structural、behavioral、visual、content、technical 五类检查均由批准 Spec/Plan 的确定性文本上下文分类；原始 Reference findings 不会变成隐藏 Requirement。
- Contract 中的每个显式 exclusion 必须逐项提供 `exclusion_ref` 和 PASS/FAIL/BLOCKED 结果。

## Evidence Strategy

- `reference_evidence` 使用 `REF-EV-*`，要求 `evaluator_owned: true`、明确证据类型、底层 Evidence ID 和来源。
- 允许复用现有 Browser Step、Runtime Check、Static Check、Content Check、Technical Check 和 Screenshot 证据；不重新获取或重新分析原始 Reference。
- 只有 Generator Handoff 或 Generator 声明不能作为 Reference Conformance 证据。

## Capability Handling

- 当前默认能力矩阵：structural、behavioral、content、technical 为 available；visual 为 unavailable。
- required capability unavailable 时结果只能为 `BLOCKED`，不允许伪造视觉 PASS；非视觉绑定不受影响。
- 未实现 Vision Provider，R6 只定义了能力状态和未来可接入边界。

## Visual Capability Status

视觉语义比对当前没有可用 Provider。需要视觉核对的绑定通过结构校验后必须进入 `BLOCKED`，并由 `reference_capability_blocked` 路由到 `SYSTEM_OR_USER/BLOCKED`。

## Explicit Exclusions

Contract 的 exclusion 列表是唯一排除检查来源。缺少逐项 exclusion evidence、出现 exclusion violation 或提交未批准 exclusion 时，不得 PASS，并按 `reference_exclusion_violation` 发送给 Generator。

## Scope Creep

结构化绑定结果中的 `scope_creep` 会生成 `reference_scope_creep`，发送 Generator。若发现批准 Contract 与 Product Spec/Plan 冲突，使用现有 `scope_mismatch` 路由到 Planner/PLANNING，不由 Evaluator 改写范围。

## Superseded Protection

R5 Contract Builder/Validator 继续拒绝 superseded、revoked、withdrawn synthesis，拒绝 avoid/exclude/revoked decision。Evaluator 还校验当前 Contract hash，旧 Contract 或过期绑定进入 `reference_stale_binding`，不得使用历史 Reference 结果冒充当前证据。

## Issue Package

已加入并注册以下分类：

- `reference_missing`
- `reference_incorrect`
- `reference_exclusion_violation`
- `reference_scope_creep`
- `reference_stale_binding`
- `reference_evidence_missing`
- `reference_capability_blocked`

现有 Evaluation Markdown 报告增加 Reference Conformance 段，Issue Package 保留
`reference_decision_id`、绑定结果、证据引用和确定性路由。

## Failure Routing

- 实现不一致、排除违反、scope creep、stale binding、普通 evidence omission → Generator/IMPLEMENTING。
- Contract/批准范围冲突 → Planner/PLANNING（使用现有 `scope_mismatch`）。
- 视觉 Provider 或环境能力不可用 → SYSTEM_OR_USER/BLOCKED。
- 没有 Contract → N/A，不进入失败路由。

## Retry Governance

R6 未新增 Retry 计数或新状态。只有 FAIL 且路由 Generator 时沿用既有
`current_iteration` 规则；BLOCKED、Planner、USER 路由不递增；达到 5 次仍进入
`WAITING_FOR_USER`，不继续自动修改。

## Context

Evaluator 只收到批准 Contract subset、批准 Spec/Plan、Generator Handoff/Response、实现证据入口和本轮 Evidence Manifest。Evaluator/Generator 的额外原始 Reference Archive 请求仍被 Path/Context Policy 拒绝；没有跨项目读取。

## Runtime / Attestation

Runtime Verifier 在现有 `evidence_manifest` 步骤校验 Contract 身份、REFDEC/AC/Task 完整性、独立证据登记、证据类型、能力和结果分类。Verifier evidence refs 绑定 Contract hash 与 Evidence Manifest evaluation ID；Attestation 进一步保存受控 Contract hash、Gate result、每个绑定结果和证据 refs。模型不能提供 Attestation ID、hash 或 Runtime 字段。

## Security

- 不允许 raw URL/HTML/screenshot/reference archive 成为实施或验收授权。
- Evidence refs 必须是项目内、已登记的结构化 ID；路径越界和未知 Evidence ID fail closed。
- Reference Contract 和 Attestation 内容继续经过 Secret/敏感字段约束；长工具输出只保留受控引用。
- R6 没有新增网络访问、Shell 执行、模型写入 project.yaml 或直接写入正式验收标准的路径。

## Backward Compatibility

没有 active Reference synthesis 的旧项目继续走原有 Evaluator 流程，新增 Gate 为 N/A。R0-R5 现有 Contract、Context、PhaseRunner、Evaluator、Issue、Transaction 和测试协议全部保持兼容。

## Tests

- R6 T01-T30: 30 passed。
- R6 + R5 + Evaluator + Context + PhaseRunner impact suite: 145 passed, 34 subtests passed。
- R0-R5 Reference protocol/analysis/contract compatibility suite: 52 passed。
- JSON Schema parse、Python compile、`git diff --check`: passed。

## Full Regression

最终代码状态下：`python -m pytest -q` → **690 passed, 5 skipped, 113 subtests passed**。

## Skipped

5 个 skipped 全部来自 `tests/test_docker_execution_environment.py`（行 107、142、182、200、218），原因相同：Docker daemon unavailable，Windows named pipe `//./pipe/dockerDesktopLinuxEngine` 不存在。没有发现其他 skipped 或失败。

## Known Limitations

- 当前没有 Vision Provider；视觉 Reference Conformance 只能 BLOCKED。
- Docker 执行环境仍依赖本机 Docker Desktop daemon。
- Web/Browser 证据可以复用既有 Browser Scenario Manifest，但 R6 不做外部 Web 重新抓取或原始 Reference 语义分析。
- Reference Conformance 依赖 Evaluator 登记可复现的独立证据；缺证据会 fail closed，不能用 Generator Handoff 替代。

## R7 Prerequisites

若未来进入 R7，至少需要用户明确授权新阶段，并先决定是否提供受控 Vision/Browser 能力 Provider、对应安全边界、证据 Schema 和环境验证；这些事项本轮不实施。

## Recommendation

DO NOT START R7。R6 已完成并达到 READY_WITH_LIMITATIONS；按当前用户指令停在 R6。
