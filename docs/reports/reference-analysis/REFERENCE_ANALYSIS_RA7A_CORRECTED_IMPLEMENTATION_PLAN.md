# RA7-A Correction — Corrected Reference Acquisition & Perception Implementation Plan

状态：待产品与计划审批  
前置纠正报告：REFERENCE_ANALYSIS_RA7A_CAPABILITY_RECONCILIATION_REPORT.md  
旧计划：REFERENCE_ANALYSIS_RA7A_IMPLEMENTATION_PLAN.md 保留不覆盖  
本文件性质：追加式修正版计划，不在本轮实施

## Plan correction summary

原计划把 Image Perception MVP 描述为“现有本地图像 artifact 加一个明确登记的感知 provider”，但没有把 Codex Host 已具备的 native image input 与 Skill Runtime 尚无 multimodal bridge 区分开。

修正版采用以下状态：

~~~text
Host multimodal input: AVAILABLE
Generic text ModelInvocationAdapter: IMPLEMENTED
Reference image -> Host model bridge: NOT_IMPLEMENTED
CodexNativeMultimodalPerceptionProvider: NOT_IMPLEMENTED
Production image perception: NOT_READY
~~~

因此不引入外部 Vision API，不新增 Vision Agent，不修改 R6，不把 Host Interactive Vision 当成生产 Reference Evidence。

## Recommended phase sequence

~~~text
Gate 0: product/plan authorization and capability contract
  -> RA7-B: Acquisition Core & Runtime Safety
  -> RA7-C: Native Image Perception Integration
  -> RA7-D: Web Browser Acquisition
  -> RA7-E: Web Multi-Evidence Perception and Fusion
  -> RA7-F: Visual Conformance Provider for R6
  -> RA8: Change Request Reference Integration
~~~

RA7-B 先建立共用安全与证据生命周期；RA7-C 再建立 Host-native Image bridge。这样不会把 Browser/Web、DOM、Screenshot、R6 Visual Conformance 与第一版 Native Image Perception 混成一个过大的阶段。

## Gate 0 — Authorization and capability contract

### Goal

在任何生产实现前确认用户批准的范围，并把三层能力、Host provenance、Attachment/local image 入口和 No-API 约束写成可测试的产品/计划输入。

### Scope

- 确认 Host AVAILABLE、Skill Integration NOT_IMPLEMENTED、Production NOT_READY 三层状态。
- 确认推荐的 Host-native path，不引入 OpenAI API key 或额外 Vision service。
- 确认 User Attachment 与 Project-local Image 的不同入口和稳定身份策略。
- 确认 Provider 只产生 structured findings，不写 status/role/CAS/project.yaml。
- 确认 RA7-B 仍不包含 Browser Acquisition、Screenshot Capture、DOM Capture、Web Acquisition、R6 Visual Conformance、PDF/Repository/Video。

### Files likely affected

- 产品方案、正式 Plan、审批记录和本轮追加报告。
- 未来实现影响面：runtime/phase_runner.py、runtime/reference_analysis、schemas/reference_analysis、runtime/context。
- 本阶段不修改生产 Python、config、schema 或 tests。

### Tests

- 只运行既有 Reference/model/context 只读验证。
- 检查三层 Capability Matrix 字段完整。
- 检查旧报告与旧计划未被覆盖。
- 执行 git diff --check。

### Pass conditions

- Product proposal、active product spec、approved plan 和 plan approval 来源链完整。
- No-API 目标明确，未引入 API key requirement。
- Host identity 未暴露的信息明确标记 not_exposed。
- 用户明确批准进入 RA7-B。

### Deferred items

- 所有 Provider、Host bridge、真实图片调用、网页采集和 R6。
- 任何生产状态转换或 project.yaml business protocol 修改。

### Stop condition

如果需要把 Host capability 写成生产 READY、需要新增 Agent、需要 API key 或需要覆盖旧 RA7-A/计划，立即停止并返回审批，不进入 RA7-B。

## RA7-B — Acquisition Core & Runtime Safety

### Goal

先建立与输入来源无关的 Acquisition/Perception 生命周期、Evidence provenance、幂等和 F11/F12/F13 边界，保证后续 Native Image 不会绕过 Runtime 安全。

### Scope

- 定义 Acquisition Protocol、Perception Protocol 和最小 Provider Registry。
- 定义 acquisition_id、perception_run_id、attempt、request fingerprint、input hash、provider/version、status、artifact refs。
- 扩展 evidence manifest，使 image artifact 可以保存 MIME、bytes、hash、scope、trust 和 provenance。
- 为 Host-native invocation 预留明确的 multimodal input contract，但不调用真实模型。
- 让 Provider 输出只能是 structured findings，不允许 runtime patch 或 workflow authority。
- 保持 text_description、当前 image normalization 和 web URL validation 向后兼容。

### Files likely affected

未来获批后可能涉及：

- runtime/reference_analysis/models.py
- runtime/reference_analysis/registry.py
- runtime/reference_analysis/module.py
- runtime/reference_analysis/artifacts.py
- runtime/reference_analysis/synthesis.py
- runtime/phase_runner.py
- runtime/context/models.py、runtime/context/policy.py
- schemas/reference_analysis/
- config/reference_analysis.yaml
- 契约测试与恢复测试

### Tests

- Schema fixtures：Acquisition Record、Perception Run、Evidence extension、REFFND v1。
- Fingerprint/hash：输入 evidence 改变、provider/version 改变、policy/schema 改变。
- Crash/retry/idempotency：staged/committed/finding committed 边界恢复。
- F10 Lease/CAS、F11 execution provenance、F12 network deny、F13 context isolation。
- Prompt Injection fixture：图像/文本中的指令不产生 tool call 或状态字段。
- R0-R6 full regression；Docker unavailable 继续如实 skip。

### Pass conditions

- 本地 text/image normalization 仍通过，旧 manifest 可兼容读取。
- 同一 input hash/provider/version 的重试不重复写业务 artifact、Event 或状态。
- Provider 输入只包含批准的 artifact/context，输出经过 schema validation。
- F11/F12/F13 的拒绝路径有可复现证据。
- 未调用真实 Host multimodal model 也能完成全部 P0/P1 契约测试。

### Deferred items

- 真实 Codex Native invocation。
- User Attachment bridge。
- Browser/Web/PDF/Repository/Video。
- R6 Visual Conformance。

### Stop condition

若 Host bridge contract 不可在不泄露 credentials、不绕过 Lease/CAS、不扩散全量 context 的前提下表达，停止 RA7-B；不得直接进入 RA7-C。

## RA7-C — Native Image Perception Integration

### Goal

把合法、hash 过的 Image Evidence 通过 Host-native multimodal bridge 交给 Codex 模型，并将结果转换成现有 REFFND；这是第一阶段真正启用视觉语义的范围。

### Scope

- 实现或接入 CodexNativeMultimodalPerceptionProvider。
- 输入为 reference_id、REFEV refs、requested domains、scope、exclusions、image artifact、trust metadata 和 perception budget。
- Provider 使用 Host 注入的 multimodal adapter；不启动外部 API，不读取 API key。
- 绑定 input_evidence_hash 到 Perception Run、output hash 和 REFFND evidence_refs。
- 输出 observed/inferred/unknown、confidence、inference_basis、unknown_reason。
- estimated_range/estimated_tolerance 只能表达视觉估计；精确 measured 仍须 deterministic evidence。
- Provider unavailable、Host provenance unknown、malformed output、timeout 和输入超预算均要显式失败或降级。
- R6 只记录未来兼容性，不在此阶段改 Gate。

### Files likely affected

- runtime/reference_analysis/models.py
- runtime/reference_analysis/registry.py
- runtime/reference_analysis/module.py
- runtime/reference_analysis/adapters/image.py
- runtime/reference_analysis/artifacts.py
- runtime/phase_runner.py
- runtime/context/models.py
- schemas/reference_analysis/reference_finding_v1.schema.json 或兼容扩展 schema
- config/reference_analysis.yaml
- Provider/integration tests

### Tests

- Host bridge contract test：image input part、MIME、artifact hash、scope 和 output mapping。
- User attachment fixture 与 project-local image fixture 分开测试，不假设底层输入相同。
- REFFND schema：observed/inferred/unknown、confidence、estimated_range、unknown_reason。
- Invocation integrity：分析 A 图不能绑定 B 图；hash mismatch 必须拒绝。
- No-authority test：Provider 返回 status/next_role/CAS/approval/project.yaml 字段必须拒绝。
- Prompt Injection image fixture：图中文字不改变任务、工具或角色。
- Host model/version unavailable：记录 not_exposed，不伪造。
- Provider unavailable/timeout/retry/idempotency。
- Visual binding unavailable：继续 BLOCKED/NOT_READY，不伪造 R6 PASS。

### Pass conditions

- Host-native input 可以通过受控 bridge 到达模型，并有可审计的 Perception Run。
- 结构化结果严格进入 REFFND validation 和现有 Synthesis。
- 每个 Finding 都能回溯到同一 REFEV hash。
- deterministic metadata 优先于 visual inference；冲突保留为 conflict。
- 无 Host bridge、无图片、无 provider 时，旧 text Reference 流程仍可用。
- 不需要额外 OpenAI API 或 API key。

### Deferred items

- Browser screenshot acquisition、DOM、Computed Style、multi-viewport。
- 多模型、模型路由、ensemble、概率校准。
- 自动产品决策、自动改稿、自动视觉评分。
- PDF、Repository、Video 和 R6 视觉验收。

### Stop condition

如果必须依赖未授权 API key、无法记录输入 hash、Host bridge 会把图片注入所有角色、模型自由文本直接进入 synthesis，或视觉估计被当作精确测量，立即停止 RA7-C 并回退到 deterministic-only。

## RA7-D — Web Browser Acquisition

### Goal

在 Image Perception 已有合法输入契约后，提供单 URL/单 viewport 的网页 Evidence Acquisition，并将截图交给已有 Native Image Perception，而不是让模型自己打开任意 URL。

### Scope

- Browser/HTTP Acquisition Provider。
- URL canonicalization、F11 execution provenance、F12 初始请求/redirect/资源授权。
- 单 HTTPS URL、默认 viewport、bounded DOM snapshot、选定 Computed Style、screenshot、console/failed requests 摘要。
- 隔离 browser context，默认无 cookies、local storage、profile、credentials。
- 每个 artifact 保存 viewport、browser/runtime identity if available、capture time 和 hash。
- 网页文字、DOM、alt、截图 OCR 仍标记 untrusted data。

### Files likely affected

- runtime/browser/adapter.py
- runtime/browser/broker.py
- runtime/browser/harness.py
- runtime/browser/models.py
- runtime/browser/policy.py
- runtime/browser/evidence.py
- runtime/reference_analysis/adapters/web_page.py
- runtime/security/network.py
- runtime/execution/broker.py
- schemas/reference_analysis/
- browser/acquisition contract tests

### Tests

- SSRF、private/loopback/metadata、DNS rebinding、redirect downgrade 和 sensitive query。
- F11/F12 integration、isolation、cookie/credential deny。
- DOM/style/screenshot bounds、stability、timeout、resource limits。
- Local fixture replay、artifact hash、viewport provenance。
- Browser dependency unavailable 时明确 skip/block，不假报成功。

### Pass conditions

- Browser 不产生 Evaluator PASS/FAIL，只产生 acquisition evidence/status。
- 每跳 redirect 与每个外部执行都可审计。
- Screenshot/DOM/style evidence 可回放并绑定 hash。
- 页面内容不能触发工具调用、状态修改或 prompt override。
- RA7-C 的 Native Image Provider 可以消费 screenshot artifact。

### Deferred items

- 多 viewport、移动设备、登录态网页、下载/上传、第三方全量资源。
- 自动视觉回归与 R6 Visual Conformance。

### Stop condition

任一导航绕过 F12、读取未授权 cookie/credential、产生未 hash 的 screenshot、无法解释 redirect 或页面内容触发 tool call，立即停止。

## RA7-E — Web Multi-Evidence Perception and Fusion

### Goal

将 measured DOM/CSS/viewport evidence 与 screenshot-based visual findings 融合，保留来源优先级、冲突和不确定性。

### Scope

- deterministic measured evidence 优先。
- Visual observation/inference 只作为带 provenance 的 Finding。
- 同一事实冲突生成 conflict/review-needed，不静默覆盖。
- unknown/unavailable/blocked 不能满足需要该能力的 binding。
- Planner 读取 synthesis 摘要，Generator/Evaluator 只读 approved contract。

### Files likely affected

- runtime/reference_analysis/synthesis.py
- runtime/reference_analysis/module.py
- runtime/context/*
- R6 conformance integration only after separate approval
- synthesis and conformance tests

### Tests

- measured vs inferred 一致/冲突矩阵。
- unknown/unavailable/blocked/invalid hash/provider version 矩阵。
- Planner/Generator/Evaluator context isolation。
- approved binding source-chain validation。
- full historical regression.

### Pass conditions

- Evidence precedence 可解释且可复现。
- 所有 conflict 和 limitations 被保留。
- 不存在 unsupported -> observed/PASS 的路径。
- 旧 Reference 合约和 Generator 来源链不被破坏。

### Deferred items

- 自动冲突解决、概率校准、跨项目共享、远程 Provider。
- 视觉评分、自动改稿、自动发布。

### Stop condition

若融合规则会静默覆盖 deterministic evidence，或 R6/approved binding 可以绕过 evidence provenance，停止并返回变更控制。

## RA7-F — Visual Conformance Provider for R6

### Goal

在 approved visual binding、Reference Screenshot、Implementation Screenshot 和完整 Evidence provenance 已存在后，扩展 R6 的视觉一致性能力。

### Scope

- 只消费 approved visual binding 与两个已 hash 的 screenshot evidence。
- Native multimodal perception 只产生 structured comparison findings。
- 视觉差异与 deterministic DOM/CSS mismatch 分开表达。
- 不能把单张图估计当作精确 PASS/FAIL 依据。
- 保留 R6 既有 PASS/FAIL 标准，不由 Provider 自行改变阈值。

### Files likely affected

- R6 conformance module/gate
- runtime/reference_analysis
- schemas and evaluator evidence manifest
- dedicated visual conformance tests

### Tests

- approved/unapproved binding、missing hash、wrong image binding。
- deterministic mismatch、visual inference、unknown、blocked。
- Host/provider unavailable 必须阻塞视觉 binding。
- R0-R6 full regression。

### Pass conditions

- R6 只使用合法 approved chain。
- Provider 无权修改 acceptance threshold 或 gate decision。
- unavailable 不会被转换成 visual PASS。
- 所有视觉结果可回溯到输入 screenshot hash。

### Deferred items

- 连续视觉评分、视频动效、跨浏览器差异自动归因。
- 自动修复 UI。

### Stop condition

如果 Provider 可以自行宣布 PASS、修改阈值、写 project.yaml 或读取未批准图片，立即停止。

## RA8 — Change Request Reference Integration

### Goal

把已经验证的 Acquisition/Perception 结果接入正式 Change Request 流程，并保持历史记录追加式和审批可撤销。

### Scope

- Reference contract versioning。
- Change Request 的 new evidence/finding/synthesis source chain。
- Product/Plan approval、Generator approved binding、Evaluator required evidence。
- 撤销、supersedes 和恢复旧 Reference 的审计路径。

### Files likely affected

- change request reference schemas/scripts
- runtime/reference_analysis
- planner/generator/evaluator integration only after their own approved plan
- reports and audit tooling

### Tests

- approve/revoke/supersede/recover。
- cross-project isolation、old evidence immutability、CAS/idempotency。
- Generator/Evaluator cannot consume unapproved perception.
- full historical regression.

### Pass conditions

- Reference 变化进入正式 Change Request，不直接修改既有批准链。
- 撤销会使执行授权失效，但不删除历史。
- 所有 evidence/finding/synthesis/provider provenance 可追溯。

### Deferred items

- 自动 Change Request 生成、跨项目 Reference marketplace、长期模型评测。

### Stop condition

若需要直接回写旧报告、删除历史 artifact、绕过 product/plan approval 或改变 role policy，停止并请求用户决定。

## MVP and Future Extensions

### MVP

MVP 推荐定义为 RA7-B + RA7-C 的最小可审计闭环：

- Acquisition/Perception Protocol、Registry、Evidence provenance 和幂等。
- Host-native multimodal bridge contract。
- 一个 CodexNativeMultimodalPerceptionProvider。
- 一个合法 project-local image artifact 输入路径。
- 一个明确的 interactive user attachment 试验路径，只有在 attachment identity 可稳定绑定时才进入正式 Reference。
- Structured REFFND、observed/inferred/unknown、estimated_range、hash binding。
- 无额外 API、无 API key、无外部 Vision service。
- 旧 text Reference 完整兼容。
- Browser/Web/R6/PDF/Repository/Video 全部延期。

将 Native Image 放进 RA7-C 而不是 RA7-B，是为了避免在 Acquisition Core 尚未有证据生命周期与 F11/F12/F13 边界时直接把图片送入 Host。这个拆分控制 scope，不是否定 Host capability。

### Future Extensions

- RA7-D Web Browser Acquisition。
- RA7-E Web multi-evidence perception/fusion。
- RA7-F R6 Visual Conformance Provider。
- RA8 Change Request Reference Integration。
- Multi-viewport、PDF、Repository、Video。
- 多 Provider、模型路由、成本/质量评测和概率校准。
- 登录态网页或受限外部来源；必须单独授权，不能成为默认能力。

## Backward Compatibility

1. text_description 继续使用现有 Reference flow。
2. image adapter 的本地文件校验、hash、size 和 PNG dimensions 继续有效。
3. web_page 仍只做 URL 安全校验，直到 RA7-D 明确开启网络 Acquisition。
4. Host/Vision unavailable、bridge unavailable、Docker skipped 都用真实状态表示。
5. Generator 继续只读取 approved plan 和 approved reference bindings。
6. 不新增 Agent、不新增 workflow state、不修改 R6 threshold、不引入 project.yaml business protocol。
7. 旧 RA7-A audit/plan 与 R0-R6 报告追加保留。

## Implementation Readiness Gate

RA7-B 只有在以下条件全部满足后才可排队：

- 用户明确批准本修正报告与 MVP 范围。
- Planner 生成新的完整产品方案并获确认。
- Planner 生成正式 plan，并在用户明确批准后生成 plan-approval 记录。
- 来源链包含 active requirements、approved proposal、design decision/skip、product approval、active product spec、approved plan 与 plan approval。
- Host bridge 的 input/output contract、provenance 和 no-API 方案被正式批准。
- 真实模型调用依赖、Host identity 暴露方式和失败降级规则有测试/替代方案。
- F11/F12/F13、Evidence schema、REFFND validation、crash/retry/idempotency 契约测试先行通过。

## Corrected plan result

~~~text
PLAN STATUS: READY_FOR_PRODUCT_REVIEW
RA7-B: Acquisition Core & Runtime Safety
RA7-C: Native Image Perception Integration
RA7-D: Web Browser Acquisition
RA7-E: Web Multi-Evidence Perception/Fusion
RA7-F: Visual Conformance Provider for R6
RA8: Change Request Reference Integration
MVP: RA7-B + RA7-C, with one Host-native image provider and no additional API/key
FUTURE: Web, R6 visual conformance, PDF, Repository, Video, multi-viewport, advanced provider routing
BACKWARD COMPATIBILITY: text_description and current deterministic image/web behavior remain usable
IMPLEMENTATION AUTHORIZATION: NOT GRANTED BY THIS PLAN
RA7-B: DO NOT START until approvals, bridge contract, security boundaries and regression evidence exist
~~~

本轮只新增本修正版计划与 Capability Reconciliation Report；未修改旧计划、生产 Python、runtime、scripts、config、schema、prompts、templates、tests、workflow、role policy 或 R6。
