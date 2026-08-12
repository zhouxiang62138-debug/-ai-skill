# RA7-A — Reference Acquisition & Perception Implementation Plan

状态：待用户确认的未来实施计划草案  
前置审计：`REFERENCE_ANALYSIS_RA7A_ACQUISITION_PERCEPTION_AUDIT.md`  
本文件性质：只规划，不在本轮实施

## 目标与边界

本计划把现有 Reference Analysis 从“来源登记、规范化和有限 deterministic 分析”逐步扩展为“受控 Acquisition、可选 Perception 和可追溯 Evidence”。它不改变当前 First-Ask、Planner、Generator、Evaluator 四段协议，也不创建第四个 Agent。

本计划遵守以下边界：

- `text_description` 现有流程必须继续可用；Browser unavailable、Vision unavailable 不能破坏文本 Reference。
- Acquisition 负责安全取得 artifact；Perception 负责解释 artifact；Synthesis 负责合并 Finding。
- Browser 只作为受控底层能力候选，不复用 Evaluator 的业务验收语义。
- 所有外部执行通过 F11，所有网络访问通过 F12，所有上下文通过 F13 的最小权限规则。
- 结果追加写入，不覆盖历史 evidence、Finding、Synthesis 或批准记录。
- 不在任何阶段伪造 Vision、伪造网页抓取成功或把 unsupported 变成 PASS。
- RA7-B 未获明确授权和完整来源链批准前，不启动任何阶段的实现。

## Recommended phase sequence

```text
P0 Contract and boundary freeze
  -> P1 Acquisition kernel and evidence lifecycle
  -> P2 Controlled Web Acquisition MVP
  -> P3 Image Perception MVP
  -> P4 Evidence fusion and conformance gates
  -> P5 Optional extensions: multi-viewport, PDF, repository, video
```

P0-P4 是 MVP 路线。P5 是 Future Extensions，不能因为 MVP 通过就自动启动。每一阶段都要先满足上一阶段的 pass conditions；任一 stop condition 触发即暂停，返回用户或产品/计划审批流程。

## P0 — Contract and boundary freeze

### Goal

把 Acquisition、Perception、Evidence、Provider 状态和 Browser/F11/F12/F13 边界定义为可测试的最小契约，同时锁定向后兼容规则。

### Scope

- 定义一个 Acquisition Protocol、一个 Perception Protocol 和一个配置驱动 Registry。
- 定义 Acquisition Record、Perception Record、artifact provenance、状态码、失败分类、attempt 和 idempotency fingerprint。
- 定义 `observed`、`inferred`、`unknown`、`conflict`、`unavailable`、`blocked` 的语义。
- 明确 Browser Harness 与 Reference Acquisition Provider 的职责分界。
- 明确 Web 的初始 URL、redirect、resource、DOM、Computed Style、screenshot evidence 范围。
- 明确 Image 的字节、像素、解码、EXIF、动画帧和模型输入预算。
- 保持 `text_description` 既有调用和产物契约兼容。

### Files likely affected

这是未来实现阶段的候选范围，不是本轮授权修改清单：

- `runtime/reference_analysis/models.py`
- `runtime/reference_analysis/registry.py`
- `runtime/reference_analysis/module.py`
- `runtime/reference_analysis/artifacts.py`
- `runtime/reference_analysis/synthesis.py`
- `schemas/reference_analysis/`
- `config/reference_analysis.yaml`
- 相关 `config/role_policies.yaml`、`config/context.yaml`、`config/browser_policy.yaml`
- 对应 runtime 与契约测试

P0 不应修改生产代码、配置、schema 或测试；上述文件仅用于未来审批后的影响面评估。

### Tests

- Protocol/schema fixtures：合法、缺字段、未知状态、过大字段、旧版本输入。
- Registry tests：未注册 provider、能力声明不匹配、版本冲突、重复注册。
- Backward compatibility：现有 `text_description` 全量回归，确保输出与历史协议兼容。
- Provenance tests：source hash、provider version、policy version、input fingerprint 的稳定性。
- Security contract tests：untrusted 标记不能产生 tool call 或角色/需求修改。

### Pass conditions

- Acquisition 与 Perception 能独立描述、独立失败、独立重试。
- 现有 text Reference 全部通过，历史结果不被覆盖。
- 所有 unsupported 能力能表达为明确状态，而不是空字符串或假成功。
- Registry、schema、状态和来源链有可复现契约测试。
- 产品方案和正式 Plan 获得用户批准；没有批准不得进入 P1。

### Deferred items

- 真实网络抓取、Playwright 安装、模型选择和 Provider 实现。
- PDF、Repository、Video、多 viewport、视觉验收。
- 大规模 provider marketplace、动态插件加载和自动模型路由。

### Stop condition

若契约需要新增第四个 Agent、覆盖既有 text schema、绕过 F11/F12/F13、读取凭据或改变验收阈值，立即停止并返回产品/计划变更控制；不得在 P0 内自行解决。

## P1 — Acquisition kernel and evidence lifecycle

### Goal

在不打开真实 Web 或 Vision 的前提下，为现有 ReferenceAnalysisModule 增加可恢复、可幂等、可审计的 Acquisition 生命周期。

### Scope

- 引入 request fingerprint、Acquisition ID、attempt、状态机和小型 checkpoint。
- 将 artifact staged、artifact committed、manifest committed 与 Finding committed 的边界明确化。
- 扩展 evidence manifest 的 acquisition/provider/environment/provenance 字段，并保持旧字段兼容。
- 让已有 text/image/web adapter 以受控的 normalization 方式接入新生命周期。
- 对外部工作保留 F11 Tool Call/Execution provenance 的接线点；没有网络时仍可运行本地 text/image 兼容路径。
- 将 F13 context 细分为 Acquisition、Perception、Planner、Generator、Evaluator 最小输入。

### Files likely affected

- `runtime/reference_analysis/module.py`
- `runtime/reference_analysis/models.py`
- `runtime/reference_analysis/artifacts.py`
- `runtime/reference_analysis/registry.py`
- `runtime/reference_analysis/adapters/text.py`
- `runtime/reference_analysis/adapters/image.py`
- `runtime/reference_analysis/adapters/web.py`
- `schemas/reference_analysis/reference_evidence_v1.schema.json` 或新增兼容版本 schema
- `runtime/execution/broker.py`、`runtime/browser/broker.py` 的最小 provenance 接线
- `runtime/security/network.py` 的调用适配，不改变 default-deny 原则
- `runtime/context/*` 的上下文裁剪测试

### Tests

- crash injection：每个 staged/committed 边界中断后恢复。
- retry/idempotency：相同 fingerprint 不重复写业务 artifact、Event、Checkpoint 或状态。
- artifact integrity：bytes、manifest、Finding、Synthesis 的 hash 和来源链。
- F10 Lease/CAS：无 Lease、过期 Lease、revision 冲突、重复恢复。
- F11/F13 contract：Provider 不得直写任意路径、项目状态或秘密上下文。
- backward regression：全部 R0-R6 相关测试与 `text_description` golden fixtures。

### Pass conditions

- 相同输入恢复是幂等的，历史工件追加且可追溯。
- 任一失败都能区分 `BLOCKED`、`UNAVAILABLE`、`FAILED` 和 `UNKNOWN`。
- 模块崩溃恢复不会重复 CAS 或重复生成业务事件。
- 原有 690 passed 基线不回退；Docker 依赖的 skipped 继续如实报告。
- 脱敏规则覆盖 URL、页面内容、日志、Event 和 artifact metadata。

### Deferred items

- 真实 Browser 导航、redirect 跟踪和模型调用。
- DOM、Computed Style、截图的正式 evidence provider。
- 复杂分布式队列、并行 provider 和远程 artifact store。

### Stop condition

若无法在本地兼容环境证明 crash/retry 幂等，或需要在事件中写入凭据/长网页内容，停止 P1；不得用“最终状态正确”替代中间状态和来源链证据。

## P2 — Controlled Web Acquisition MVP

### Goal

在显式授权和可复现浏览器环境下，采集单个 HTTPS URL、单个默认 viewport 的最小网页证据包。

### Scope

- 选择一个受控 Web Acquisition Provider，优先复用现有 Browser Adapter 的底层动作。
- 只允许单 URL、单默认 viewport、有限时间/字节/节点/资源预算。
- 采集最终 URL、每跳 redirect、bounded HTML/DOM snapshot、screenshot、选定 Computed Style、console/failed requests 摘要。
- 初始 URL、redirect、DNS/连接 IP 和页面资源执行 F12 授权；外部动作留存 F11 provenance。
- 浏览器 context 默认无 cookies、local storage、个人 profile 和 credentials。
- 将网页内容标记为 untrusted data，并将页面内容与系统指令分离。

### Files likely affected

- `runtime/browser/adapter.py`
- `runtime/browser/broker.py`
- `runtime/browser/harness.py`
- `runtime/browser/models.py`
- `runtime/browser/policy.py`
- `runtime/browser/evidence.py`
- `runtime/reference_analysis/adapters/web.py`
- `runtime/reference_analysis/module.py`
- `runtime/security/network.py`
- `runtime/execution/broker.py`
- `config/browser_policy.yaml`
- `config/reference_analysis.yaml`
- `schemas/reference_analysis/`
- Web acquisition integration/contract tests

### Tests

- URL/redirect SSRF matrix：loopback、private、metadata、DNS rebinding、scheme downgrade、敏感 query。
- Browser isolation：cookies、local storage、profile、credentials 不泄露。
- bounded capture：超时、响应大小、DOM 节点、资源数、截图大小和 redirect 次数。
- deterministic evidence：相同 fixture 的 canonical URL、DOM/样式选择器、截图和 hash。
- F11/F12/F13 integration：Tool Call/provenance、NetworkPolicy decision、untrusted context。
- local fixture app acceptance；没有真实浏览器依赖时必须明确 skip/block 原因。

### Pass conditions

- 只有明确授权的 HTTPS URL 可以进入采集；每跳 redirect 都可审计。
- 采集产物均有 artifact ref、hash、viewport、captured_at、provider/version 和 policy decision。
- Browser acquisition 不能产生 Evaluator PASS/FAIL，只能产生 evidence/status。
- 禁止 cookies/credentials/SSRF/Prompt Injection 的测试全部通过。
- 可在固定 fixture 上重放并得到相同 fingerprint 与结构化结果。

### Deferred items

- 多 viewport、移动设备模拟、视觉回归评分。
- 任意用户网站、登录态网页、支付/个人信息页面。
- 页面内任意脚本执行、文件下载、上传、弹窗自动处理。
- 抓取全量网络资源、视频、字体或跨域第三方内容。

### Stop condition

任何一次导航绕过 F12、读取到未授权 cookie/credential、无法解释的 redirect、未 hash 的截图、页面内容触发工具调用，立即停止 P2 并废弃该次采集 attempt；不得将其标记为可用 evidence。

## P3 — Image Perception MVP

### Goal

在已有本地图像 artifact 安全边界之上，接入一个明确登记、版本固定、可审计的 Perception Provider，并保持 deterministic metadata 与模型推断分层。

### Scope

- 复用现有 Image adapter 的格式、大小、魔数、哈希和尺寸校验。
- 增加解码后最大像素/内存预算、必要的 EXIF/颜色空间/透明度处理规则。
- 只注册一个小范围 Provider；输入必须是已提交、不可变、hash 校验的 image artifact。
- 输出 typed findings，携带 provider/version、input hash、状态、uncertainty 和 evidence refs。
- 视觉不可用、超时、超预算、格式不支持时显式返回 unavailable/blocked/unknown。

### Files likely affected

- `runtime/reference_analysis/adapters/image.py`
- `runtime/reference_analysis/models.py`
- `runtime/reference_analysis/registry.py`
- `runtime/reference_analysis/module.py`
- `runtime/reference_analysis/synthesis.py`
- `schemas/reference_analysis/`
- `config/reference_analysis.yaml`
- Perception provider contract/golden tests

### Tests

- 格式/魔数/大小/像素/解码/压缩炸弹/动图帧预算矩阵。
- Provider registry、版本、输入 hash 和 output schema 校验。
- deterministic metadata 与 inferred perception 分离。
- provider unavailable、timeout、quota、malformed output、重复重试。
- R6 visual binding gate：无 provider 时必须 BLOCKED，不得 PASS。

### Pass conditions

- Provider 只能读单个批准 artifact，不能联网、读任意项目路径或取得 credentials。
- 输出可由 input hash 和 provider/version 重放，或明确标记非 deterministic 并记录参数。
- Vision findings 不覆盖尺寸/哈希等 deterministic evidence。
- provider 不可用时现有 image/text 流程仍可用，依赖视觉的 binding 保持阻塞。

### Deferred items

- 多模型 ensemble、自动模型选择、训练/微调、视频帧感知、OCR 全量管线。
- 视觉相似度、自动评分、自动改稿或自动生成产品决策。

### Stop condition

若 Provider 的来源、版本、模型参数、成本预算或输出 schema 不能审计，或模型输出被当作 observed fact，停止 P3；回退到 deterministic-only。

## P4 — Evidence fusion and conformance gates

### Goal

把 deterministic evidence、Perception Finding 和现有 Synthesis 连接起来，确保冲突、unknown 和 unsupported 都不会被静默吞掉。

### Scope

- 明确同一事实的 source precedence：deterministic observed 优先，perception inferred 带 provenance。
- 多来源一致时形成 synthesis；不一致时生成 conflict/review-needed。
- 把 acquisition/perception provenance 纳入 R6 Conformance Gate 和 approved binding 校验。
- 使 Planner 只获得已批准的 synthesis 摘要，Generator 只获得 approved binding。

### Files likely affected

- `runtime/reference_analysis/synthesis.py`
- `runtime/reference_analysis/module.py`
- `runtime/reference_analysis/artifacts.py`
- `runtime/context/*`
- R6 conformance/gate implementation and schemas
- `config/role_policies.yaml`
- synthesis/gate/regression tests

### Tests

- deterministic 与 Vision 一致、冲突、缺失、unknown、unavailable、blocked 的矩阵。
- provenance chain 断裂、hash 不符、provider 版本变化、schema 变化。
- Planner/Generator/Evaluator context isolation。
- approved binding 在 evidence 不足或 Vision 不可用时拒绝通过。
- 全量历史回归和可复现报告。

### Pass conditions

- 不存在把 unavailable/unknown 转换成 observed/PASS 的路径。
- 所有 conflict 保留各来源，不静默覆盖。
- approved binding 只来自合法来源链，Generator 无法读取未批准原始 archive。
- 历史 Reference 与 R0-R6 回归无破坏性变化。

### Deferred items

- 自动冲突解决、概率校准、跨项目 Reference 共享、远程 provider。
- 复杂多轮网页采集和主动探索。

### Stop condition

若融合规则无法解释、来源链缺失或 R6 gate 可被绕过，停止 P4；不能通过放宽阈值或扩大上下文来掩盖证据缺口。

## P5 — Future Extensions

P5 仅作为未来路线，不是 MVP 的隐含交付项：

| 扩展 | 计划边界 | 额外安全/资源要求 |
|---|---|---|
| Multi-viewport | 多个固定 viewport、device scale/UA 版本化 | 组合爆炸预算、每 viewport 独立 artifact/hash |
| PDF | 有界页数/对象/渲染像素的 PDF artifact 与页面 evidence | 禁止默认跟随外链，防解析/解码资源耗尽 |
| Repository | 显式 repo/ref/commit 的隔离 archive | F11 process、F12 host、临时目录、symlink/文件数/大小预算 |
| Video | 有界视频与采样帧/音频 transcript | 时长、分辨率、帧数、音频与临时空间预算 |
| Advanced Vision | 多 provider、模型路由、视觉相似度 | provider trust、成本、版本、可解释性和人工复核 |

每项扩展都必须重新走产品方案、正式 Plan、计划批准和来源链校验；不能由 P4 PASS 自动开启。

## MVP 与 Future Extensions 的明确边界

### MVP

- 一个 Acquisition Protocol、一个 Perception Protocol、一个 Registry。
- F11/F12/F13 的接线与最小运行时状态/幂等能力。
- 保持当前 text Reference。
- 单个受控 HTTPS Web URL、单默认 viewport、bounded DOM/样式/截图/redirect evidence。
- 当前本地 Image artifact 加一个明确登记的感知 provider；Provider 不可用时 deterministic-only 仍可工作。
- 证据 manifest、artifact hash、provider/version、冲突与 unavailable 语义。
- 完整契约测试、fixture 回放、crash/retry/idempotency、R6 gate 回归。

### Future Extensions

- 多 viewport 与移动设备矩阵。
- PDF、Repository、Video。
- 多模型/多 Provider、自动路由、概率校准、视觉相似度。
- 登录态和用户授权站点；即使未来支持，也必须是显式独立授权，不得成为默认能力。
- 自动产品决策、自动改稿、自动验收评分。

## Backward Compatibility

设计上必须确保：

1. `text_description` 原有 Reference 流程继续可用。
2. `image` 和 `web_page` 当前的安全校验与 unavailable/deferred 语义不被删除或改成假成功。
3. 新 Evidence 字段尽量可选并版本化；旧 manifest 可以只读迁移/兼容读取，禁止覆盖历史。
4. Browser unavailable、Vision unavailable、Docker skipped 都如实显示，不阻止不依赖这些能力的文本流程。
5. Generator 继续只读取 `approved_plan` 和合法 approved bindings；新的 raw acquisition 不得成为隐式实施输入。

## Implementation Readiness Gate

RA7-B 只有在以下条件全部满足后才可排队：

- 用户明确批准 RA7-A 结论、MVP 范围与外部网络授权边界。
- Planner 追加完整产品方案版本并获用户确认。
- Planner 追加正式 `plan-<nnn>.md`；用户再次明确批准并生成 `plan-approval-<nnn>.md`。
- 来源链包含 active requirements、approved proposal、design decision/skip、product approval、active product spec、approved plan 与 plan approval。
- Browser/Playwright 运行依赖、隔离策略和 Docker/本地 fixture 验证环境已具备，或明确记录受控替代方案。
- F11/F12/F13 接线、Evidence schema、Provider Registry 和状态机的契约测试先行通过。

## RA7-A Plan Result

```text
PLAN STATUS: READY_FOR_PRODUCT_REVIEW
MVP: P0-P4, with one controlled Web provider and one explicitly registered Image perception provider
FUTURE: P5 multi-viewport/PDF/Repository/Video/advanced Vision extensions
BACKWARD COMPATIBILITY: text_description remains usable; unavailable capabilities remain explicit
IMPLEMENTATION AUTHORIZATION: NOT GRANTED BY THIS AUDIT
RA7-B: DO NOT START until product and plan approvals plus blocking security/runtime evidence exist
```

本轮只新增本计划文件和对应审计报告；未修改 production Python、runtime、scripts、config、schema、prompts、templates、tests、workflow 或 role policy，未启动 RA7-B。
