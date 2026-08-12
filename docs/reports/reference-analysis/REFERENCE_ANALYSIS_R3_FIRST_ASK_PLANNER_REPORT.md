# Phase R3 — First-Ask & Planner Reference Integration Report

## R3 RESULT:

PASS — READY_WITH_LIMITATIONS

R0、R1 与 R2 的前置报告已保留并作为本轮来源。R3 完成了正常用户输入进入 First-Ask 后的参考检测、登记、范围/模式记录、Reference Analysis 路由和 Planner synthesis 来源链；未启动 R4。

## Files changed:

本轮 R3 主要改动：

- `runtime/intake/__init__.py`
- `runtime/intake/first_ask.py`
- `runtime/reference_analysis/artifacts.py`
- `runtime/reference_analysis/module.py`
- `runtime/orchestrator.py`
- `runtime/context/builder.py`
- `runtime/context/policy.py`
- `runtime/policy.py`
- `scripts/project_state.py`
- `scripts/reference_planner.py`
- `config/context.yaml`
- `config/reference_analysis.yaml`
- `config/role_policies.yaml`
- `prompts/planner_prompt.md`
- `templates/product_proposal.md`
- `tests/test_reference_analysis_r3.py`

R0–R2 已有的协议、schema、适配器、分析器、Artifact Store 和测试工件保持在当前工作区，未删除历史文件。

## First-Ask Integration:

- 新增可执行 `FirstAskIntakeModule`，正常入口为 `process_user_message()`。
- First-Ask 只保存用户原话、引用登记、scope、mode、context 和需求快照，不生成 evidence、analysis、Finding 或 synthesis。
- 原始用户输入以追加式 `memory/requirements/reference-request-<hash>.md` 保存，并由需求快照引用。
- First-Ask 在 `INTAKE` 和显式用户输入唤醒的 `WAITING_FOR_REQUIREMENTS` 中运行；等待态仍不会自动启动角色。
- 每轮只执行检测和登记，不要求用户手动调用 register/analyze/synthesize。

## Reference Detection:

- 支持 `http/https` URL、项目目录内且经授权的本地图片，以及明确的产品/网站/应用/布局/导航等参考描述。
- 已命名的 `Linear`、`Notion` 等引用登记为 `text_description`，不会自动联网发现。
- 模糊的“以后可能参考别的产品”等表达不会登记引用。
- 同一消息中的多个明确命名引用可分别生成 `REF-*`；多个 URL 也分别登记。
- R3 没有新增 PDF、Video、Repository、Figma、Screen Recording、Browser 或 Vision 适配器。

## Registration:

- 每个引用通过 R2 `ReferenceArtifactStore` 写入 `source-001.yaml` 与 `scope-001.yaml`。
- First-Ask 使用独立的 `first_ask_intake` Path Policy，只允许写需求快照、用户请求和 `memory/references/` 登记工件。
- First-Ask 不能写 `artifacts/references/` 下的 evidence、analysis、Finding 或 synthesis；Reference Analysis Module 仍独占这些分析工件。
- 需求快照的 `references` 条目包含 `reference_id`、source/scope pointer、三态 scope、mode、显式 include/exclude、context 和注册状态。

## Workflow Routing:

- 需求达到 `sufficient_for_planning` 且存在 active references 时，First-Ask 通过 Runtime CAS 转移到：

  ```yaml
  status: REFERENCE_ANALYSIS
  active_module: reference_analysis
  next_role: null
  reference_status: ready
  reference_analysis_status: running
  ```

- Reference Analysis 完成后仍由 Runtime CAS 转移到：

  ```yaml
  status: PLANNING
  active_module: null
  next_role: planner
  active_reference_synthesis: memory/references/synthesis/...
  ```

- 没有引用时保留旧路径 `INTAKE -> PLANNING`；需求不充分时保留现有 `WAITING_FOR_REQUIREMENTS`。
- Runtime 新增了显式用户输入唤醒 First-Ask 的入口，但没有放宽等待态自动启动角色的规则。

## Planner Source Chain:

- Planner 继续读取 `active_requirements`。
- 当 `active_reference_synthesis` 非空时，Planner 只读取该 synthesis；默认不重新抓取 URL、不读取原始图片、不回读全量历史 Finding。
- `scripts/reference_planner.py` 提供只读 synthesis 加载、Planner 决定选择、Reference Integration 渲染和追溯校验。
- synthesis 中的候选决定不会自动变成产品决定；必须由 Planner 明确选择对应 `REFDEC-*`。

## Planner Prompt:

`prompts/planner_prompt.md` 已明确：

- Reference 只是 Planner 输入，不是第四个 Agent，也不是需求本身。
- 用户需求、明确约束、显式排除和已批准产品工件优先于 Reference。
- `requires_planner_resolution` 冲突不得随机选择；需要组合决策或进入现有用户澄清等待态。
- Planner 不得修改 source、scope、evidence、finding 或 synthesis。

## Product Proposal Integration:

`templates/product_proposal.md` 新增 `Reference Integration`，包含：

- References Used
- Adopt
- Adapt
- Avoid
- Unknown / Not Used
- Traceability

产品方案只能记录 Planner 明确采用或改造的决定，并必须保留 `REFDEC` 以及后续 Finding/source 追踪。

## Traceability:

正式链路为：

```text
Product Proposal decision -> REFDEC-* -> REFFND-* -> REFEV-* / Reference source
```

`validate_proposal_reference_traceability()` 会拒绝缺少 `Reference Integration`、漏列 synthesis 来源或引用未知 `REFDEC-*` 的 Proposal。Reference synthesis 本身不会自动写入 Proposal 决定。

## Context Builder:

- 新增 `first_ask_intake` 与 `reference_analysis` 的 module context policy。
- First-Ask Context 可读取当前需求和受控 reference catalog。
- Reference Analysis Context 可读取已登记来源、scope、mode、include/exclude 和安全 locator。
- Planner Context 新增 `active_reference_synthesis`，不新增原始 URL/图片/全量历史 Finding 注入。
- `reference_catalog` 只包含受控元数据，不包含原始 HTML、图片二进制或长篇历史分析。
- Module Context 使用 `ActorType.MODULE`、模块 Capability 和模块 Path Policy；Planner 仍是三 Agent 之一。

## Ownership:

- First-Ask：原始用户请求、requirements snapshot、reference source/scope registration。
- Reference Analysis Module：adapter normalization、evidence、analysis、Finding、synthesis 及分析状态。
- Planner：读取 active synthesis、选择产品决策并写 Proposal；不改 Reference 分析工件。
- Generator、Evaluator 和 Change Request 的 Reference 消费留给后续阶段；R3 没有把完整 Reference 上下文注入它们。
- Core Agent 仍只有 Planner、Generator、Evaluator；Orchestrator、Context Builder、Lease 和 Recovery 都不是 Agent。

## Idempotency:

- First-Ask 用 `project_id + normalized user message` 的 SHA-256 作为注册请求指纹。
- 同一消息重复提交不会生成第二组 `REF-*` 或第二份需求快照，并会返回原引用记录。
- 同一 context 添加第二个引用会保留 `REF-001`、`REF-002`，由 R2 synthesis fingerprint 生成新 synthesis 版本。
- 撤销引用使用追加式 `source-002.yaml`，旧 `source-001.yaml` 保留，最新记录为 `superseded` 后不再进入 active source 列表。

## Recovery:

- First-Ask 请求和引用工件均为追加式；写入中断后稳定请求指纹可识别已登记记录，避免重复登记。
- Reference Analysis 继续沿用 R2 的 artifact-before-CAS、幂等 synthesis、crash recovery 和 Runtime CAS fencing。
- 撤销不会删除或覆盖历史 source；下一次分析根据剩余 active sources 生成新 synthesis，并通过 `supersedes` 保留 synthesis 历史链。

## Tests:

- R3 + R2 定向测试：`14 passed`。
- R3 覆盖检测保守性、URL/命名引用、三态 scope、显式排除、mode/default、First-Ask 路由、旧无引用路由、幂等、Planner 追溯、Context catalog 和撤销追加式记录。
- 纯逻辑与策略检查：通过。

## Regression:

- 完整 pytest 回归：`637 passed, 5 skipped, 113 subtests passed`。
- 最终回归耗时：`76.23s`。
- `git diff --check`：通过。
- 覆盖范围包含 R0–R2、First-Ask、workflow、schema、policy、Runtime routing、CAS/Lease/fencing、Context Builder、Planner、Planner approval、Design、Generator/Evaluator、Change Request、security、recovery 和全量 pytest。

## Known Limitations:

- 图像视觉语义分析和布局语义分析仍为 R2 已知限制，当前只做安全登记、完整性检查和 capability unknown 记录。
- Web acquisition 仍为 deferred；R3 不自动抓取网页，也不把网页内容写入 Event Payload 或 Context。
- 浏览器截图、Vision、PDF/Video/Repository/Figma 等能力没有在 R3 启用。

这些限制属于 R3 允许的 READY_WITH_LIMITATIONS 范围；不存在“仍需手工操作后 Planner 才能看到 synthesis”、只保存 raw-only reference 或 Reference 覆盖用户需求的阻塞问题。

## R4 Prerequisites:

- 用户明确确认当前 R3 结果并提出 R4 目标。
- 如 R4 处理图像语义、网页采集或浏览器截图，需先定义版本化 adapter/capability、受控证据格式、网络/路径边界、失败分类和对应回归测试。
- R4 不得回写或覆盖 R0–R3 的历史报告、source、scope、Finding 或 synthesis。

## Recommendation:

R3 集成可进入用户评审；在用户明确提出并批准下一阶段目标前，保持当前状态，不自动进入 R4。

DO NOT START R4
