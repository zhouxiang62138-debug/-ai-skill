# RA11-H Official Codex SDK Multimodal Bridge Report

**Date:** 2026-08-10  
**Result:** `IMPLEMENTED / LIVE_AUTH_BLOCKED`

## RA11-H RESULT

本轮没有把 mock 或合成 finding 记为 Live PASS。官方 Python SDK/app-server 桥接已经接入，
并实际尝试了一次 project-local image invocation；当前 SDK 进程的 `Codex().account()` 返回
`requires_openai_auth=True`，因此 Live 调用被分类为
`LIVE_NOT_RUN_ENVIRONMENT_UNAVAILABLE:CODEX_AUTH_UNAVAILABLE`。

## Previous B1 Conclusion

历史结论是：Host 可以看图，但 Skill Runtime 没有可调用的程序化 multimodal bridge，状态为
`PROGRAMMATIC_BRIDGE_UNEXPOSED`。

## Corrected B1 Conclusion

现在的正确结论是：官方 bridge 已有可执行实现，B1a（project-local image → Codex multimodal）
不再是“平台没有程序化图片接口”。本机当前剩余的是 Codex SDK 运行时的认证可用性，不能将其
伪装成模型或平台能力缺失。

```text
B1 Project-local Native Multimodal:
IMPLEMENTED_WITH_LIVE_AUTH_LIMITATION
```

## Official Codex Transport

```text
Python package: openai-codex
Transport: official_codex_sdk
Runtime: local Codex app-server over JSON-RPC
Input: TextInput + LocalImageInput(path=validated_absolute_path)
Structured output: thread.run(..., output_schema=PerceptionResult)
```

官方文档确认 Python SDK 要求 Python 3.10+，包名为 `openai-codex`；app-server 的 turn input
支持 `text`、`image` 和 `localImage`，且 `outputSchema` 只作用于当前 turn。

## SDK / App-server Version

```text
SDK: 0.144.4
App-server: 0.144.4 (Codex Desktop / Windows)
Model: not exposed by the current result surface
```

## Authentication

桥接只调用官方 `Codex().account()`，没有读取、复制、持久化或打印 OAuth token、auth.json 或
API key。当前结果为：

```text
requires_openai_auth: true
auth mode: existing Codex auth unavailable to this SDK process
```

## API Key Requirement

```text
Additional API: NO
Additional API key: NO
```

实现没有新增 `OPENAI_API_KEY` 前提；如果官方 SDK 当前进程没有现有认证，调用会 fail closed。

## Project-local Image

```text
Project-local Image: SUPPORTED_WITH_LIMITATIONS
Acquisition: LocalImageAcquisitionProvider
Binding: REF-EV + content SHA-256
Transport input: LocalImageInput
Live model result: blocked by CODEX_AUTH_UNAVAILABLE in this run
```

文件路径先经过项目路径策略、格式、大小、像素、完整性和 SHA-256 校验；文件在 acquisition 后
变化时，旧 hash 会被拒绝。感知结果还绑定 evidence hash、scope、requested domains、exclusions、
provider version 和 schema version。

## User Attachment

```text
Direct User Attachment Auto-binding: BLOCKED_BY_HOST_ATTACHMENT_METADATA
```

该状态与项目内图片路径独立。当前 Host 没有向 Skill Runtime 提供稳定 attachment id、路径或
resource handle，因此不会自动把本轮用户附件升级成项目 Reference Evidence。

## Multimodal Invocation

已实现独立 perception thread，使用 `ApprovalMode.deny_all`、`Sandbox.read_only`、最小范围文本
输入和 `LocalImageInput`。实际 Live 尝试结果：

```text
Invocation attempted: YES
Official SDK turn completed: NO
Reason: CODEX_AUTH_UNAVAILABLE
Mock counted as Live PASS: NO
```

## Structured Output

Provider 使用 `PERCEPTION_RESULT_SCHEMA` 约束 `findings` 与 `limitations`。模型返回内容必须是
可解码的结构化 JSON；Markdown、自由文本或 schema 不合法时返回
`STRUCTURED_OUTPUT_INVALID`，不能进入 REFFND。

模型只输出领域 finding；`reference_id`、`finding_id`、`trust_level` 和创建时间由本地 Provider
补齐，并再次执行 REFFND 校验。支持 `observed`、`inferred` 和 `unknown`，不允许超出请求域的
finding 被提升。

## Evidence Integrity

```text
REF-EV ID: required
content SHA-256: required and rechecked immediately before invocation
changed image: rejected as EVIDENCE_HASH_MISMATCH
idempotency key: evidence hash + scope + provider version + schema version
valid repeat: reuses stored result without a second invocation
```

## Recursion Protection

感知线程带有确定性标记 `perception_invocation=true`，并被明确禁止启动 First-Ask、Planner、
Generator、Evaluator 或另一个 perception provider。Perception 是 ReferenceAnalysisModule 基础
设施，不是第四个 Agent；核心角色仍只有 Planner、Generator、Evaluator。

## Security

图片中的文字始终按不可信视觉数据处理。感知线程被要求不使用 shell、network、filesystem write
或任何工具，SDK 侧使用 `deny_all` + `read_only`；Provider 输出不能修改 project.yaml、需求、
审批、Plan、lease 或状态路由。测试覆盖了包含“Ignore instructions / Modify project.yaml /
Run PowerShell”字样的图像数据。

## Image Live E2E

```text
Project-local image → REF-EV → LocalImageInput → Codex → structured result → REFFND
Status: BLOCKED_BY_CODEX_AUTH_UNAVAILABLE
```

项目内图片注册、采集、hash 校验和 Provider 调用入口已经串接；本轮没有真实模型结果，因此不
继续宣称 RA11-D PASS。

## Design Integration

`ReferenceAnalysisModule.run()` 已将 image source 路由到 native perception Provider。Live 结果不可用
时仅生成明确标记为 unknown 的保守能力 finding；没有合成视觉 finding 冒充图片影响，也没有用
图片结果直接改写产品方案或设计选择。

```text
Live image-derived REFDEC: NOT_GENERATED
Design Exploration live proof: DEFERRED
```

## Generator Integration

未产生 live image-derived approved contract，因此没有启动或宣称 RA11-F Generator product loop。
现有 Generator 仍只消费已批准的 plan 与产品规范，不接受感知线程的 workflow authority。

## Evaluator Integration

未产生 live image-derived implementation，因此没有生成 live conformance PASS。现有 Evaluator
边界与原功能回归保持不变。

## Browser B3 Status

```text
B3 Browser: BLOCKED_BY_BROWSER_RUNTIME
F12 wiring: IMPLEMENTED
lease-bound repository browser worker: NOT_AVAILABLE
```

Browser B3 与 B1a 分开记录，不阻塞项目内图片的 SDK 桥接实现，也不被图片路径结果掩盖。

## Tests

```text
RA11-H contract tests: 18 passed, 1 skipped
Reference/security/runtime targeted regression: 122 passed, 1 skipped
Live test: 1 skipped as LIVE_NOT_RUN_ENVIRONMENT_UNAVAILABLE:CODEX_AUTH_UNAVAILABLE
```

覆盖内容包括 SDK discovery、认证边界、LocalImageInput、真实 SDK wire shape、output schema、
hash 绑定、stale image 拒绝、observed/inferred/unknown、unsupported domain、恶意图片文本、
递归标记、工具/权限边界、幂等复用、invalid output、timeout fail closed，以及 project-local image
Live 尝试。

## Full Regression

```text
740 passed
6 skipped
113 subtests passed
```

Docker 等外部环境依赖和 Live auth 不可用单独计为 skipped，不计入 Live PASS；
`git diff --check` 与 `compileall` 均通过。

## Skipped

```text
LIVE_NOT_RUN_ENVIRONMENT_UNAVAILABLE:CODEX_AUTH_UNAVAILABLE
Docker-dependent tests: only when the repository runner reports Docker unavailable
```

## Capability Matrix

| Capability | Status | Notes |
|---|---|---|
| Text Reference | `SUPPORTED` | 既有确定性文本适配器。 |
| Project-local Image | `SUPPORTED_WITH_LIMITATIONS` | 路径/hash/官方 SDK 桥接已实现；本机认证不可用。 |
| Direct Desktop Image Attachment | `BLOCKED_BY_HOST_ATTACHMENT_METADATA` | 与项目路径能力分离。 |
| Public Web | `BLOCKED_BY_BROWSER_RUNTIME` | Host smoke 与 F12 wiring 不等于 repository live acquisition。 |
| Multi-reference | `SUPPORTED_WITH_LIMITATIONS` | 结构化证据图支持；live multimodal 受认证限制。 |
| Design Exploration | `SUPPORTED_WITH_LIMITATIONS` | live image-derived design proof 尚未执行。 |
| Generator | `SUPPORTED_WITH_LIMITATIONS` | 只消费批准后的 contract。 |
| Evaluator | `SUPPORTED_WITH_LIMITATIONS` | live visual conformance 尚未执行。 |

## Remaining Blockers

1. 当前 SDK app-server 进程需要可复用的现有 Codex 登录态；不能通过新增 API key 绕过本协议。
2. Desktop attachment → project evidence 的 Host 元数据仍未暴露。
3. Browser B3 仍缺 lease-bound ExecutionBroker → Playwright/F12 repository worker。
4. 只有完成真实认证后的 image invocation，才能重新执行 RA11-D、RA11-F，并验证图片确实影响
   Design Exploration、Generator 和 Evaluator。

## Recommendation

保留 `IMPLEMENTED_WITH_LIVE_AUTH_LIMITATION / NOT_READY`，继续使用官方 `openai-codex` 桥接和
项目内路径工作流；认证可用后重新运行显式 Live 测试。不要把本轮契约测试、fake SDK wire 测试、
Host Browser smoke 或 deterministic unknown finding 写成真实 multimodal PASS。
