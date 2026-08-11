# Reference Analysis R2 Core Module Report

日期：2026-08-09

## R2 RESULT:

PASS（READY_WITH_LIMITATIONS）

R2 Core Module 已实现、接入现有 F10 Runtime 控制面，并通过定向测试与完整回归。限制仅来自真实未启用的 Vision、Browser Capture、PDF、Video、Repository 等后续适配能力，不构成 R2 阻塞。

## Files changed:

- `config/reference_analysis.yaml`
- `config/role_policies.yaml`
- `config/workflow.yaml`
- `config/schemas/project_v6.schema.json`
- `config/schemas/reference_*_v1.schema.json`
- `runtime/execution/path_policy.py`
- `runtime/orchestrator.py`
- `runtime/policy.py`
- `runtime/reference_analysis/`
- `scripts/project_state.py`
- `scripts/reference_protocol.py`
- `templates/reference_*.yaml`
- `templates/project.yaml`
- `templates/requirements_snapshot.yaml`
- `tests/test_reference_analysis_r2.py`

此前 R0/R1 报告及 R1 协议、Schema、模板、测试文件均保留，未覆盖历史工件。

## Core Architecture:

Reference Analysis 是受 Runtime 控制的单一 Module，不是第四个 Agent。核心角色集合仍只有 Planner、Generator、Evaluator；First-Ask 仍是 Intake Module，不进入 `next_role`。

Module 内部拆分为 Registry、Adapter、Normalized Reference、Domain Analyzer、Artifact Store 和 Synthesis Engine，避免按 `source_type` 堆叠巨型条件分支。

## Module Location:

`runtime/reference_analysis/`

入口为 `ReferenceAnalysisModule`，由现有 `runtime.orchestrator.Orchestrator` 选择和托管。

## Adapter Architecture:

- `ReferenceAdapterProtocol` 约束来源适配器接口。
- `ReferenceRegistry` 从 `config/reference_analysis.yaml` 动态加载适配器和分析器。
- 未注册或未启用的 Adapter fail closed。
- `text_description` 使用 `TextDescriptionAdapter`。
- `image` 使用 `ImageAdapter`。
- `web_page` 使用 `WebPageAdapter`。

## Normalized Reference:

内部模型统一包含 `reference_id`、`source_type`、规范化来源摘要、内容哈希、scope、证据引用、可用模态、能力、限制、适配器版本和 `untrusted` 信任级别。

原始文本不会进入 Runtime Event；只在受路径策略保护的分析阶段短暂使用，事件只保存工件路径、哈希、大小和运行指纹。

## Supported Sources:

- `text_description`
- `image`
- `web_page`

R1 中的 screenshot、PDF、video、repository、source_code、existing_project 等仍为 reserved，不在 R2 激活。

## Actual Analysis Capability:

- 文本描述：真实读取 UTF-8 文本，校验大小、哈希和路径，并通过确定性领域分析器生成 observed / unknown Findings。
- 图片：校验项目内路径、存在性、PNG/JPG/JPEG/WebP 格式、Magic Bytes、大小、SHA-256 和 PNG 尺寸元数据；不伪造视觉语义结论。
- 网页：校验 HTTPS、主机、私有地址、凭据、片段和敏感查询参数；不直接请求网络。
- 图片输出明确 `visual_semantic_analysis: unavailable` 和 `READY_FOR_FUTURE_VISION_ADAPTER`。
- 网页输出明确 `NETWORK_ACQUISITION_DEFERRED` 和 `BROWSER_CAPTURE_DEFERRED`。

## Synthesis Engine:

`ReferenceSynthesisEngine` 基于 Findings、scope、reference mode 和用户优先级策略确定性生成 `adopt`、`adapt`、`avoid`、`unknown`。

多来源相同领域出现不一致 observed Finding 时写入 `REFCON-*`，状态为 `requires_planner_resolution`，不静默选择来源。合成结果明确声明用户需求和已批准产品工件优先，Reference Decision 不是产品需求。

## Artifact Store:

`ReferenceArtifactStore` 统一处理：

- ID 分配和版本递增；
- project / change request 路径构造；
- R1 Schema / Protocol 校验；
- 原子写入、SHA-256 和大小记录；
- 已存在目标 fail closed；
- source、scope、analysis、findings、evidence manifest、synthesis 的追加式保存。

标准项目路径保持为：

```text
memory/references/reference-001/
  source-001.yaml
  scope-001.yaml
  analysis-001.yaml
  findings/REFFND-001.yaml
  evidence/manifest-001.yaml
memory/references/synthesis/reference-synthesis-001.yaml
artifacts/references/reference-001/evidence/manifest-001.yaml
```

项目状态只保存 `active_reference_synthesis` 相对指针，不保存原始 Reference 内容。

## Runtime Module CAS:

- `module_authorization` 从 `config/role_policies.yaml` 读取，当前允许 `first_ask_intake`、`reference_analysis`、`change_request`。
- Module 不创建第二套 CAS，复用现有 Worker Lease、Fencing、Revision、Compare-And-Swap、Ownership、Event、Checkpoint 和 Recovery 基础设施。
- `Orchestrator.start()` 对 Module 做配置白名单和源状态校验。
- `commit_module_step()` / `commit_module_state()` 支持复用已持有 Lease，避免模块内重复抢占 Lease。
- Reference Analysis 通过 CAS 从 `REFERENCE_ANALYSIS` 进入 `PLANNING`，由 Runtime 设置 `next_role=planner`。
- Module 没有直接写 `project.yaml` 的路径；失败状态也通过 CAS 写入。

## Security:

- 模块读写经过 `ExecutionPathPolicy.assert_module_path`。
- 拒绝绝对路径、盘符、`..`、符号链接、junction / reparse point、控制面路径和跨项目上下文。
- `project.yaml` 直接写入始终拒绝，必须经过 CAS。
- 文本输入保持 untrusted；“忽略规则”等提示注入文本只作为观察数据，不能改变执行路径或 Runtime 状态。
- 网页适配器不调用 `requests.get` 或其他直连网络 API；私有地址、localhost、元数据主机和敏感查询参数 fail closed。
- 适配器和分析器只从配置注册，未知类型拒绝。

## Crash / Idempotency:

- 工件提交前崩溃：项目状态保持不变。
- 工件已提交、CAS 前崩溃：重试通过运行指纹发现既有 synthesis，不重复生成 `analysis-002/003` 或重复工件。
- CAS 已提交后重复调用：返回幂等结果，不重新生成工件。
- 指纹由协议版本、分析版本、来源哈希、scope 哈希、适配器和分析器配置组成。
- 失败不会生成假的 completed synthesis；业务状态提交只经过现有 CAS。

## Tests:

R2 定向及相关回归：

```text
40 passed
```

覆盖文本端到端、scope 排除、需求优先级策略、图片安全注册、路径逃逸、网页不安全 scheme、Registry、未知 Adapter、Finding / Synthesis 校验、多来源冲突、幂等重试、崩溃恢复、无引用等待状态和三角色集合。

## Regression:

完整回归：

```text
631 passed, 5 skipped, 113 subtests passed
```

`git diff --check` 通过。

## Known Limitations:

- 当前没有真正的 Vision Model，因此图片只做安全注册和能力报告。
- 当前没有 Browser Capture / 网络采集能力，网页只做 URL 合同校验和 deferred 能力声明。
- PDF、Video、Screen Recording、Repository、Source Code、Design File 和 Existing Project 适配器未在 R2 激活。
- Change Request 引用工件路径已支持隔离存储，但把其 synthesis 投影到 Change Request 专属业务状态仍交给后续 Change Request Module；不会写入新项目的全局 synthesis 指针。
- R2 只提供 Reference Module 内部的最小上下文摘要，不接入完整 R3 Planner Context Builder，也不修改 Planner / Generator / Evaluator 业务提示词。

## Deferred Capabilities:

- Vision Semantic Adapter
- Browser / Acquisition Interface 的真实受控实现
- PDF / Video / Repository / Source Code / Design File adapters
- 更丰富的测量、布局、组件和跨来源模式检测
- R3 Planner 消费 Reference Synthesis 的正式上下文链路

## R3 Prerequisites:

1. 用户明确确认 R2 报告和 R2 的已知限制。
2. 保持 Reference Schema、Finding、Synthesis、Conflict 和 Traceability 记录稳定。
3. 为 Planner 设计正式的 Reference Context Builder 输入边界，明确不把 untrusted Reference 当作 Agent 指令。
4. 在 R3 设计集成前，继续保持 Generator、Evaluator 和现有 Planner 业务提示词不变。
5. 为真实 Vision / Browser 能力分别补充独立的权限、证据和恢复测试。

## Recommendation:

R2 已达到 `READY_WITH_LIMITATIONS`，但本轮按用户要求在 R2 报告后停止，不自动进入 R3。

## DO NOT START R3

本报告完成后停止当前任务，等待用户明确启动 R3。
