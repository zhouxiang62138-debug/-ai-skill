# RA7-A Correction — Codex Native Multimodal Capability Reconciliation

审计日期：2026-08-09  
前置报告：REFERENCE_ANALYSIS_RA7A_ACQUISITION_PERCEPTION_AUDIT.md  
前置计划：REFERENCE_ANALYSIS_RA7A_IMPLEMENTATION_PLAN.md  
报告性质：追加式能力纠正与架构对账；不实施生产能力

## Previous RA7-A conclusion

旧 RA7-A 报告写的是：

~~~text
Current Vision Capability: NOT_AVAILABLE
~~~

这个结论对“Skill 仓库中是否已经存在正式 VisionProvider/ImagePerceptionProvider”是成立的，但把三个不同层次压成了一个状态，因此不够准确。本报告不删除、不覆盖旧报告，只追加宿主能力与 Skill 接入能力的正式区分。

## Why clarification was required

OpenAI 官方 Codex 文档说明，Codex/ChatGPT 的交互输入可以包含截图、图表和视觉参考；官方 GPT-5-Codex 模型资料也把 Image 列为 Input-only modality。[Codex Image Inputs](https://learn.chatgpt.com/docs/image-inputs) 明确描述了在桌面/Web/Codex CLI 中附加图片，[GPT-5-Codex 模型文档](https://developers.openai.com/api/docs/models/gpt-5-codex) 明确列出图片输入能力。

这证明的是 Host/Model 层“可以接收图像并进行视觉理解”的能力，不证明本仓库已经把 project-local image 绑定到 ReferenceAnalysisModule，也不证明已经有正式 Provider、REFFND 校验、来源链、幂等和安全接线。

## Three-layer reconciliation

| 层次 | 正确状态 | 审计依据 | 含义 |
|---|---|---|---|
| Layer 1 — Host Multimodal Capability | **AVAILABLE** | Codex Image Inputs 文档、GPT-5-Codex modality 文档、当前 Codex Host 输入面 | 宿主可以接收用户提供的图像/截图并让模型进行视觉分析；具体模型 snapshot 由 Host 管理，Skill 仓库不伪造。 |
| Layer 2 — Skill Invocation Integration | **NOT_IMPLEMENTED** | runtime/phase_runner.py 的通用 ModelInvocationAdapter 只有文本化 Context；没有 image/file content part 或 multimodal request contract | 仓库有模型调用抽象，但没有 ReferenceAnalysisModule -> image input -> host model -> structured result 正式路径。 |
| Layer 3 — Production Reference Perception | **NOT_READY** | 没有正式 Provider、REFFND 视觉产物、Provider provenance、输入 hash 绑定和 R6 视觉 Gate 接线 | 不能把宿主可看图当作生产 Reference Perception 已就绪。 |

补充判断：CodexNativeMultimodalPerceptionProvider 架构上可实现，当前尚未实现。推荐状态为 READY_FOR_INTEGRATION，不是 READY，也不是 NOT_AVAILABLE。

## Host Multimodal Capability

### 结论

~~~text
Host Multimodal Capability: AVAILABLE
Host can receive image input: YES
Host can perform visual reasoning: YES, at the model/input-surface layer
Exact model snapshot exposed to Skill: NO / HOST-MANAGED
~~~

当前 Codex Host 能处理用户在会话中提供的图片、截图和视觉参考。模型可以对图像进行描述、布局理解、层级分析和受约束的视觉推理；这属于当前会话的输入能力，不是 Skill 自己注册的 Provider。

官方 OpenAI Images and Vision 文档同时说明，支持视觉的模型可以分析图片输入，并可使用 URL、Base64 data URL 或 file ID 作为 API 输入。但本轮不把 API 示例当作仓库已经有 API 接线的证据；本轮选择 Host-native 路径，不引入外部 API。

### 能力证据与不确定性

- 官方文档确认模型/Host 产品面支持图片输入。
- 当前运行上下文显示本任务由 Codex GPT-5 系列 Host 执行，但 Skill 仓库不掌握最终 model snapshot、底层 provider identity 或内部 attachment token。
- 因而 Provider provenance 必须记录 Host 在运行时真实暴露的信息；没有的信息写 not_exposed，不得猜测模型版本。

## User Attachment Capability

### 结论

~~~text
User-attached image visible to Host model: AVAILABLE
User-attached image understood by Host model: AVAILABLE at interactive Host layer
Stable attachment identity inside current Reference Protocol: NOT_FORMALIZED
~~~

用户直接附图并要求“参考这张截图的布局和视觉层级”时，Host 可以把图片作为本次对话输入交给模型。当前任务附件是文本文件，Host 将它暴露为一个本地 attachment path；但仓库没有发现统一的 image attachment ID、MIME、bytes hash 或 attachment-to-Reference binding 协议，不能假设文本附件与图片附件在 Skill Runtime 中完全同构。

因此，用户附件可以先支持一个明确标记的 interactive_attached_image 路径：

~~~text
Host attachment
  -> Host-owned attachment identity
  -> bounded image evidence reference
  -> CodexNativeMultimodalPerceptionProvider
  -> structured REFFND
~~~

进入正式 Reference Protocol 前必须固定：attachment identity、来源会话、MIME/格式、大小、SHA-256、作用域、捕获时间、用户授权和过期策略。仅仅“模型看得到”不能成为 REFEV-*。

## Project-local Image Capability

### 当前已具备

runtime/reference_analysis/adapters/image.py 可以在模块路径策略下读取项目内合法图像，校验文件存在、大小、格式魔数、SHA-256，并解析 PNG 尺寸。它能产生 deterministic image metadata。

### 当前缺失

仓库的 ModelInvocationRequest 只有：

~~~text
session_id, run_id, invocation_id, role, phase, context, required_steps
~~~

context 是 Context Manifest，ContextSource 也只建模文本 inline content 或 reference；没有受控的 image bytes、MIME、image URL、file ID、attachment ID 或 multimodal content part。runtime/cli.py 也明确要求宿主注入 ModelInvocationAdapter，CLI 不接受任意模型 Callable。

所以当前准确状态是：

~~~text
Project-local image normalization: READY_WITH_LIMITATIONS
Project-local image -> model input: NOT_IMPLEMENTED
Project-local image semantic Reference: NOT_READY
~~~

路径安全、跨项目隔离、控制面拒绝、大小限制和格式校验仍然有效；不能为了接入模型而绕过这些边界。

## User Attachment 与 Project-local Image 的差异

| 项目 | User Attachment | Project-local Image |
|---|---|---|
| 入口 | Host 会话输入面 | Skill Module 文件路径 |
| 当前模型可见性 | Host 层可见 | Skill 可读取/哈希，但不能自动注入模型 |
| 稳定身份 | Host-owned attachment identity，仓库未建模 | project-relative artifact ref + SHA-256 |
| 安全边界 | 会话、用户授权、附件大小/MIME | path policy、project isolation、module capability、大小/格式 |
| 进入 Reference Protocol | 需要 attachment binding bridge | 需要 multimodal invocation bridge |
| 当前生产状态 | 可交互分析，未正式绑定 | deterministic metadata 可用，视觉接入未实现 |

两条路径不应为了统一接口而伪装成同一种底层对象。最终可以在 Evidence 层统一为 artifact_ref + hash + provenance，但采集入口和授权来源必须保留差异。

## Programmatic Skill Invocation Capability

### 当前状态

~~~text
Generic text ModelInvocationAdapter: IMPLEMENTED
Reference Analysis -> model invocation: PARTIAL for text-only role phases
Reference image -> model invocation: NOT_IMPLEMENTED
Codex native multimodal programmatic bridge: NOT_YET_AVAILABLE
~~~

当前 phase_runner 确实提供模型调用生命周期：创建 invocation、构建 Context、调用注入的 ModelInvocationAdapter、校验结构化返回、记录状态并做 CAS/attestation。但这个通用路径没有把图片作为模型输入建模，也没有 Reference Analysis 专用 Perception invocation。

因此旧报告里的“Formal Skill Vision Provider: NOT_AVAILABLE”应修正为：

~~~text
Host Interactive Vision: AVAILABLE
Programmatic Skill Vision Invocation: NOT_YET_AVAILABLE
Formal Skill Perception Provider: NOT_IMPLEMENTED
Production Reference Image Perception: NOT_READY
~~~

## No-API Feasibility

~~~text
Additional OpenAI API: NOT REQUIRED for the recommended Host-native path
Additional API key: NOT REQUIRED for the recommended Host-native path
External Vision service: NOT RECOMMENDED / NOT AUTHORIZED by this correction
~~~

原因是当前目标是直接利用 Codex Host 已有的多模态输入，而不是另起一个 OpenAI API 客户端或外部 Vision 服务。正式 Provider 应由 Host 注入一个能接受 image evidence + structured task 的 multimodal adapter；这需要补 Skill-to-Host bridge，不需要自动引入 API key。

注意：如果未来选择独立调用 OpenAI API，那是另一种部署方案，可能需要 API key、网络策略、成本和凭据管理；本轮不选择、不实现，也不把 API key 写入任何配置或计划前置条件。

## Recommended Provider Architecture

推荐名称：CodexNativeMultimodalPerceptionProvider。它属于 Perception Provider，不是 Agent。

~~~mermaid
flowchart LR
  M[ReferenceAnalysisModule] --> P[CodexNativeMultimodalPerceptionProvider]
  P --> I[Host Multimodal Invocation Adapter]
  I --> C[Codex Native Multimodal Model]
  C --> O[Structured Provider Output]
  O --> V[REFFND Validation]
  V --> S[Existing Reference Synthesis]
  E[REFEV image evidence + SHA-256] --> P
  X[F13 bounded context] --> P
  P -. no status/role/CAS writes .-> G[Runtime Governance]
~~~

不新增 VisionAgent、ImageAgent 或 ReferenceVisionAgent。仍然只保留 Planner、Generator、Evaluator 三个 Core Agents；Reference Analysis 仍是 Module。

### Provider input

输入至少包含：

~~~yaml
reference_id: REF-001
evidence_refs: [REFEV-001]
requested_domains: [layout, visual_hierarchy, component_recognition]
scope: "desktop screenshot only"
explicit_exclusions: [responsive_behavior, interaction, motion, technical_architecture]
image_evidence:
  artifact_ref: artifacts/references/REFEV-001.png
  sha256: "<real sha256>"
  mime: image/png
trust_metadata:
  trust_level: untrusted
perception_budget:
  max_context_bytes: 131072
  max_output_bytes: 65536
~~~

Provider 不接收整个 project.yaml、所有历史 Reference、完整 Runtime State、凭据或任意项目路径。

## Structured Output Contract

Provider 不能返回自由聊天文本。它必须生成与现有 config/schemas/reference_finding_v1.schema.json 兼容的结构化 Finding，至少包括：

~~~yaml
schema_version: 1
finding_id: REFFND-001
reference_id: REF-001
domain: layout
category: navigation_structure
observation:
  value: left_sidebar
epistemic_status: observed
confidence: high
evidence_refs: [REFEV-001]
user_scope_status: unspecified
trust_level: untrusted
created_at: "<runtime timestamp>"
~~~

视觉判断中无法由图片证明的事实必须使用 unknown 并提供 unknown_reason；模型推断必须使用 inferred 并提供 inference_basis。结果经过 schema validation、Evidence hash binding 和现有 synthesis，不直接修改项目状态。

## Epistemic Safety

### observed / inferred / unknown

- observed：图像中可直接观察且与当前 scope 相符的现象，例如“画面左侧存在垂直导航区域”。
- inferred：模型根据视觉线索作出的解释，例如“该区域可能是 sidebar”。
- unknown：静态图不能证明的交互、响应式行为、动效、技术实现或不可见状态。

### Estimated values

视觉估计不能冒充精确测量。当前 Finding schema 已支持 measurement 的 measured、estimated_range 和 estimated_tolerance。例如只能估计侧栏宽度时：

~~~yaml
observation:
  value: sidebar_width
  measurement:
    value_type: estimated_range
    minimum: 220
    maximum: 250
    unit: px
    confidence: medium
epistemic_status: inferred
~~~

只有 DOM/CSS/像素标尺等 deterministic evidence 才能使用 measured；单张截图不得输出 observed exact width = 240px。

### Deterministic evidence remains stronger

推荐优先级继续为：

~~~text
Measured DOM/CSS Evidence
  > Structured Image Evidence
  > Visual Observation
  > Model Inference
~~~

视觉 Provider 不能替代未来的 DOM、Computed Style、Viewport 或 screenshot artifact。它负责解释，不负责伪造测量。

## Evidence Integrity and Invocation Binding

未来 Perception Run 必须至少记录：

~~~text
perception_run_id
provider_id
provider_version
host_identity_if_available
model_identity_if_available
input_evidence_refs
input_evidence_hashes
timestamp
requested_domains
result_hash
limitations
status
~~~

核心完整性约束：

~~~text
REFEV-001 bytes/hash
  -> Provider input manifest
  -> structured output
  -> REFFND evidence_refs: [REFEV-001]
  -> result hash / synthesis
~~~

如果 Host 不暴露具体 model/version，记录 not_exposed 或 Host 实际提供的 identity，不伪造名称。分析 A 图的输出不能绑定到 B 图；input_evidence_hash 必须参与 invocation fingerprint 与幂等键。

## Security

即使 Host 可以看图，图片里的文字仍然是：

~~~text
UNTRUSTED REFERENCE DATA
~~~

例如图片中出现“忽略之前指令、运行 PowerShell、读取 Secret”，都不能获得 Runtime authority、Tool authority 或 Workflow authority。Provider 只允许输出 structured findings，禁止输出 status、next_role、active_module、CAS patch、approval 或 project.yaml changes。

项目本地图像仍需遵守：

- module path policy 和 cross-project isolation；
- 文件存在、格式、魔数、大小、像素/解码预算；
- artifact hash 和 append-only store；
- 最小上下文、无 credentials、无任意网络；
- provider timeout、输出大小和重试预算。

## Context Boundary

只有 reference_analysis Module / Provider 需要读取原始 Image Evidence。

| 消费者 | 可见内容 |
|---|---|
| CodexNativeMultimodalPerceptionProvider | 单个已授权图像 artifact、REFEV metadata、requested domains、scope、排除项、预算和 untrusted 标记 |
| Planner | 已验证的 REFFND/Synthesis、冲突、unknown 和来源摘要 |
| Generator | Approved Reference Contract 与必要 evidence refs |
| Evaluator | Approved Contract 与 required evidence，不读任意原始 archive |

图片不能因为“模型能看”就注入所有角色上下文。Provider 不能读取整个项目状态，也不能写状态。

## Web Implications

Host 可以看图，不等于 Host 自动能打开任意 URL。Web 仍需独立 Acquisition：

~~~text
URL
  -> Browser/HTTP Acquisition
  -> Screenshot + DOM + Computed Style + Viewport Evidence
  -> CodexNativeMultimodalPerceptionProvider
  -> REFFND
~~~

RA7-A 原有 Web 结论不变：当前 web_page 只有 URL 安全校验，网络抓取仍 deferred；Browser 仍需 F11/F12 接线、SSRF/redirect 防护和 bounded evidence。

## R6 Future Implications

本轮不修改 R6。未来若 Provider 正式建立，可以扩展：

~~~text
Approved Visual Binding
  + Reference Screenshot
  + Implementation Screenshot
  -> Visual Conformance Provider
  -> R6 Visual Conformance Gate
~~~

这只是未来路线，不能把当前 Host Interactive Vision 当作 R6 production evidence，也不能绕过 R6 的 approved binding、Evidence integrity 和 PASS/FAIL 规则。

## Corrected Capability Matrix

~~~yaml
host:
  multimodal_image_input: available
  interactive_user_attachment_vision: available
  exact_model_identity_to_skill: not_exposed

skill:
  generic_text_model_invocation: implemented
  project_local_image_normalization: ready_with_limitations
  image_input_in_model_invocation: not_implemented
  user_attachment_to_reference_binding: not_formalized

provider:
  codex_native_multimodal_perception_provider: not_implemented
  provider_architecture: ready_for_integration

production:
  image_semantic_reference_analysis: not_ready
  visual_r6_conformance: deferred

security:
  image_prompt_injection_authority: denied
  credential_access_from_image: denied
  project_cross_boundary_image_access: denied
~~~

因此不再使用单一的 vision: unavailable 来描述全部情况。

## Updated RA7 Implementation Sequence

1. **RA7-B — Acquisition Core & Runtime Safety**：先落地 Acquisition/Perception boundary、Evidence lifecycle、F11/F12/F13 接线与幂等，不启用视觉语义。
2. **RA7-C — Native Image Perception Integration**：增加受控 multimodal input bridge、CodexNativeMultimodalPerceptionProvider、structured REFFND validation、provenance 和 R6-compatible unavailable gate。
3. **RA7-D — Web Browser Acquisition**：单 URL、单 viewport、bounded screenshot/DOM/style evidence；继续保持 SSRF、redirect、cookie 和 Prompt Injection 边界。
4. **RA7-E — Web Multi-Evidence Perception/Fusion**：融合 measured evidence 与视觉 inferred findings，保留冲突和 unknown。
5. **RA7-F — Visual Conformance Provider for R6**：仅在 approved visual bindings 与真实 evidence 链完整后扩展。
6. **RA8 — Change Request Reference Integration**：按正式 Change Request 协议接入已批准的 Reference 变更。

本序列不新增 Agent、不新增 workflow state，不修改当前 R6，不自动引入 API key。

## 必须回答的关键问题

| # | 问题 | 回答 |
|---:|---|---|
| 1 | Codex 当前 Host 模型能否看图？ | 能。Host/模型输入层为 AVAILABLE；官方 Codex Image Inputs 与 GPT-5-Codex 文档提供依据。Skill 不掌握最终 snapshot。 |
| 2 | 用户直接上传图片时能否被模型理解？ | 能，在交互 Host 层可以；但当前没有稳定 attachment-to-Reference binding。 |
| 3 | Skill 能否程序化把 project-local image 加入模型 invocation？ | 当前不能。能规范化和哈希本地图像，但 ModelInvocationRequest 没有 image/file content part。 |
| 4 | 两种图片路径是否相同？ | 不相同。User Attachment 是 Host 会话输入；local image 是项目受控 artifact。最终 Evidence 可统一，入口身份和安全策略必须分开。 |
| 5 | 是否需要额外 OpenAI API？ | 推荐 Host-native 路径不需要。需要补 Host bridge，而不是自动新建 API 客户端。 |
| 6 | 是否需要 API key？ | 推荐 Host-native 路径不需要。本轮不引入 API key requirement。 |
| 7 | 是否需要额外 Vision Model？ | 不需要另购/另接模型来证明宿主能力；当前 Host 已具备图像输入。是否使用特定 snapshot 应由 Host 真实暴露并记录。 |
| 8 | 是否可以实现 CodexNativeMultimodalPerceptionProvider？ | 可以，架构上可行；当前 Provider 未实现，生产状态 NOT_READY。 |
| 9 | 它应该位于哪个架构层？ | 位于 Reference Analysis Module 之下的 Perception Provider 层，不是 Agent。 |
| 10 | 它如何输出 REFFND？ | 接收受控 image evidence，输出符合 reference_finding_v1.schema.json 的 structured Finding，经 validation 后进入 synthesis。 |
| 11 | 如何记录 observed/inferred/unknown？ | 直接使用现有 epistemic_status，并分别补充 inference_basis 或 unknown_reason。 |
| 12 | 如何处理 estimated values？ | 使用 estimated_range/estimated_tolerance 和 confidence；精确 measured 只能来自 DOM/CSS 等 deterministic evidence。 |
| 13 | 如何防图片 Prompt Injection？ | 图片文字永远是 untrusted data；Provider 无 tool/runtime/workflow authority，不能执行图中指令。 |
| 14 | 如何绑定 Evidence hash？ | Provider input manifest、invocation fingerprint、REFFND evidence refs 和 result hash 都绑定同一 REFEV hash。 |
| 15 | 如何控制 Context？ | Provider 只见一个已授权 artifact、范围、排除项、预算和 provenance；Planner/Generator/Evaluator 继续读取分层摘要/批准契约。 |
| 16 | RA7-A 原 Vision NOT_AVAILABLE 应如何修正？ | 改为：Host Interactive Vision AVAILABLE；Programmatic Skill Vision Invocation NOT_YET_AVAILABLE；Formal Provider NOT_IMPLEMENTED；Production Image Perception NOT_READY。 |
| 17 | RA7-B 是否现在可以启动？ | 不可以。必须先完成产品/计划批准、Host bridge 契约、Evidence/Provider schema、安全边界和非生产接线验证。 |

## Blocking Issues

1. Skill 没有 image-aware ModelInvocationRequest 或 Host multimodal adapter contract。
2. User Attachment 没有进入 Reference Protocol 的稳定 identity/hash/binding 规范。
3. Project-local image 尚未有受控的 artifact -> multimodal input 桥接。
4. 没有正式 CodexNativeMultimodalPerceptionProvider、provider provenance 和 structured output validator 接线。
5. 当前 Host 不向 Skill 仓库保证具体 model/version；Provider 必须接受 runtime provenance 可部分未知。
6. R6 visual conformance 还没有建立 approved visual evidence 链；本轮不能修改 R6。

## Recommendation

修正 RA7-A 的文字结论，但不把宿主能力误报成生产能力：

~~~text
Host Multimodal Capability: AVAILABLE
User-attached image support: AVAILABLE at interactive Host layer
Project-local image support: normalization READY_WITH_LIMITATIONS; model injection NOT_IMPLEMENTED
Programmatic multimodal invocation: NOT_YET_AVAILABLE in Skill Runtime
Formal Skill provider: NOT_IMPLEMENTED
Production image perception: NOT_READY
Additional API required: NO for Host-native path
Additional API key required: NO for Host-native path
Corrected Vision status: Host AVAILABLE / Integration NOT_IMPLEMENTED / Production NOT_READY
Recommended provider: CodexNativeMultimodalPerceptionProvider
Updated RA7 phases: RA7-B Acquisition Core -> RA7-C Native Image -> RA7-D Web -> RA7-E Fusion -> RA7-F R6 Visual -> RA8
Blocking issues: multimodal bridge, attachment/artifact binding, provenance, validation, approvals
RA7-B readiness: NOT_READY
Recommendation: DO NOT START RA7-B
~~~

## RA7-A CORRECTION RESULT

~~~text
RA7-A CORRECTION RESULT: PASS
Host multimodal capability: AVAILABLE
User-attached image support: AVAILABLE at interactive Host layer
Project-local image support: READY_WITH_LIMITATIONS for normalization; NOT_IMPLEMENTED for model injection
Programmatic multimodal invocation: NOT_YET_AVAILABLE
Formal Skill provider: NOT_IMPLEMENTED
Production image perception: NOT_READY
Additional API required: NO for recommended Host-native path
Additional API key required: NO for recommended Host-native path
Corrected Vision status: AVAILABLE / NOT_IMPLEMENTED / NOT_READY by layer
Recommended provider: CodexNativeMultimodalPerceptionProvider
Updated RA7 phases: RA7-B -> RA7-C -> RA7-D -> RA7-E -> RA7-F -> RA8
Blocking issues: multimodal bridge, attachment/artifact binding, provenance, validation, approvals
RA7-B readiness: NOT_READY
Recommendation: DO NOT START RA7-B
~~~

本轮只新增 Correction Report 与其对应的修正版实施计划；不修改旧 RA7-A 报告，不修改生产 Python、runtime、scripts、config、schema、prompts、templates、tests、workflow、role policy 或 R6。
