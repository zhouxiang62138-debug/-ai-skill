# R5 Generator Reference Integration Report

## R5 RESULT

PASS — READY_WITH_LIMITATIONS

本轮仅实施 R5 Generator Reference Integration。R6 未启动。

## Generator Source Authority

Generator 的确定性来源优先级为：

```text
Approved Plan
  > Approved Product Spec
  > Approved Acceptance Criteria
  > Approved Product / Design Decisions
  > Approved Reference Bindings
  > Reference Synthesis
  > Raw Reference（永不作为实施权威）
```

`REFDEC` 只有在 Planner 集成、产品批准、正式 Product Spec、批准 Plan 和 Plan Approval
完成后，才可以通过绑定进入 Generator。`REFDEC != Implementation Requirement`。

## Approved Reference Contract

已新增确定性的 `Approved Reference Contract` 构建和校验层：

- `scripts/reference_contract.py`：无副作用构建 canonical contract、contract id/hash 和严格校验。
- `runtime/reference_contract.py`：只按现有 Execution Path Policy 读取当前批准链和当前 Synthesis，向 Runtime 返回受控摘要。
- `config/schemas/reference_contract_v1.schema.json`：契约 schema v1。
- 不新增平行的 `approved_reference_contract.yaml` 持久化状态文件；契约从当前批准 Spec/Plan 派生，避免第二套事实源。
- 契约只包含 `REFDEC`、`REFFND`、`REF`、REQ、Spec、Task、AC、来源链、版本、适用范围和显式排除，不包含 raw URL、HTML、截图、图片二进制或原始证据正文。

支持无 Reference、Reference 存在但未采用 REFDEC、部分采用、多 Reference 和确定性幂等。悬空 REFDEC、过期/撤销 Synthesis、显式排除冲突、缺少 Finding 或 AC 追踪均 fail closed。

## Preflight Integration

复用既有 Generator `contract_preflight` Phase Step，没有新增平行 Phase Step。Generator Preflight 现在同时校验既有高风险 Implementation Contract 和 Approved Reference Contract，并把 contract hash 写入受控 evidence refs。

Context 构建后再次核对契约 source hash、contract id 和 Preflight 结果；不一致时拒绝进入模型调用。模型输出不能携带 `reference_contract` 或运行时字段。

## Traceability

每个采用的 `REFDEC` 均绑定：

```text
REFDEC -> REFFND / REF -> Product Spec -> Plan Task -> Acceptance Criteria
```

Contract 保留 `source_chain`、版本指针、`applies_to`、`exclusions`、Product Spec refs、Plan refs、AC refs 和 Requirement refs。未在批准 Spec/Plan 中出现的 REFDEC 不会进入契约。

## Context Strategy

Generator Context 仅增加 `approved_reference_bindings` 摘要源，并把其 canonical hash 纳入 Context Manifest。Generator 默认不会读取 raw Reference、完整 Reference archive、旧 Synthesis 或全部 Evidence；显式追加原始 Reference archive、Synthesis 或 Evidence 路径会被拒绝。普通 `code/` 和测试文件追加引用保持兼容。

## Explicit Exclusions

Generator 不得实现 `avoid`、`exclude`、`revoked` 或与用户显式排除冲突的 REFDEC；契约中的 exclusions 不是可选建议，而是实现边界。Reference 数据只作为不可信输入，不能改变 Requirement、验收阈值、产品范围或 Runtime 状态。

## Superseded Reference Protection

`superseded`、`revoked`、`withdrawn` Synthesis 被拒绝。Contract source chain 绑定当前 `active_reference_synthesis`、Synthesis ID、Product Approval、Product Spec、Approved Plan 和 Plan Approval，并由 contract hash、Context hash、Verifier evidence 和 Attestation 间接绑定运行版本。

## PhaseRunner / Verifier

- PhaseRunner 继续使用既有 `contract_preflight`，并把 reference contract 传入 Runtime Verifier。
- `contract_preflight` Verifier 确定性校验 contract fields、hash、source chain、binding IDs 和 trace IDs。
- Handoff Verifier 要求逐条报告 `REFDEC -> Task -> AC`，状态只能是 implemented、not_implemented、blocked 或 deviation_detected。
- Handoff 拒绝未知/缺失/额外绑定、Task/AC trace mismatch，以及 Reference Conformance PASS、Visual Similarity PASS、Reference Fidelity PASS 或 Evaluation PASS 声明。

## Attestation

没有改变 Session/Event/CAS/Lease schema。Approved Reference Contract 通过 Context Manifest source hash、Verifier evidence refs（含 contract hash）和现有 Phase Attestation hash 纳入不可变证明；Contract 变化会改变 Context/Verifier/Attestation 结果。

## Handoff

Handoff 模板新增 Implemented Reference Bindings、Not Implemented / Excluded 和 Deviations 字段说明。Generator 只报告事实、Task/AC 引用和验证证据，不宣称 Reference Conformance 或 Evaluator PASS。

## Runtime/Security

Generator 不直接写 `project.yaml`，不修改 Reference Synthesis、Approved Plan、Product Spec、Approval Record 或评分规则；现有 Runtime CAS、Worker Lease、Fencing、Recovery 和 append-only 约束保持不变。Path Policy 控制 Module 读取 Synthesis，Generator 只能通过 runtime adapter 获得绑定摘要；Secret scanner 的 `sk-*` 误报边界也已收紧，避免误阻断合法 `TASK-*` 标识。

## Backward Compatibility

无 Reference、Reference 未采用、现有 Generator Contract、旧 Handoff 和现有 PhaseRunner 流程保持兼容。只有当前项目存在 active Synthesis 且批准 Spec/Plan 明确采用 REFDEC 时，才生成绑定摘要并启用逐条 Handoff 校验。

## Tests

- R5 定向：`python -m pytest -q tests/test_reference_contract_r5.py` → `14 passed`。
- Reference Protocol + R5：`29 passed`。
- 影响范围回归（R3/R4/Context/PhaseRunner/R5）→ `50 passed`。
- 覆盖无 Reference、未采用、批准绑定、部分采用、多 Reference、悬空/过期/排除拒绝、版本/hash/幂等、恶意证据隔离、Context 原始路径拒绝、Verifier/Handoff 追踪和 PASS 声明拒绝。

## Full Regression

执行：

```text
python -m pytest -q
660 passed, 5 skipped, 113 subtests passed
```

完整回归覆盖 Generator、Preflight、PhaseRunner、Verifier、Attestation、Context、Budget、Workflow、Schema、Role Policy、Approval、Design、First-Ask、Runtime、CAS、Lease、Fencing、Recovery、Security、Execution、Evaluator、Change Request 及 R0-R4 Reference 测试。

## Skipped

5 个 skipped 全部来自 `tests/test_docker_execution_environment.py`，原因是 Windows Docker daemon / named pipe 不可用。`python -m pytest -q -rs` 已确认没有新增 R5、Reference、Generator 或 Runtime skipped。

## Known Limitations

- Docker execution tests 仍受本机 Docker daemon 不可用限制。
- Image semantic analysis、Web acquisition、Browser Capture、Vision 和其他 deferred Adapter 未在 R5 启用。
- R5 的 Reference Contract 是确定性来源链与绑定协议，不等同于真实视觉相似度评估，也不替代 Evaluator。
- Contract 的 Project Spec / Plan refs 在缺少显式 PS/TASK 标识时会保留批准文件指针；仍要求 AC 和 Finding 追踪存在。

## R6 Prerequisites

R6 前仍需用户明确授权，并先定义下一阶段的版本化能力边界、持久化/迁移策略、证据格式、网络/浏览器/视觉权限、失败分类和完整回归方案。任何 deferred Vision/Web/Browser 能力不得因 R5 PASS 自动启用。

## Recommendation

DO NOT START R6

R5 已达到 `PASS — READY_WITH_LIMITATIONS`，本轮在此关闭。
