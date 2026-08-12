# Phase R1 — Reference Analysis Protocol & Schema Report

## R1 RESULT

PASS

R1 已完成协议、Schema、配置、模板、工作流契约、项目状态投影、角色归属、确定性校验和回归测试。本阶段没有启动真实网页、图片、视觉、仓库、PDF、视频或浏览器分析，也没有创建第四个 Agent。

## Files changed

本轮新增或修改：

- `config/reference_analysis.yaml`
- `config/workflow.yaml`
- `config/role_policies.yaml`
- `config/change_request.yaml`
- `config/schemas/project_v6.schema.json`
- `config/schemas/reference_source_v1.schema.json`
- `config/schemas/reference_scope_v1.schema.json`
- `config/schemas/reference_analysis_v1.schema.json`
- `config/schemas/reference_finding_v1.schema.json`
- `config/schemas/reference_evidence_v1.schema.json`
- `config/schemas/reference_synthesis_v1.schema.json`
- `scripts/project_state.py`
- `scripts/reference_protocol.py`
- `templates/project.yaml`
- `templates/requirements_snapshot.yaml`
- `templates/reference_source.yaml`
- `templates/reference_scope.yaml`
- `templates/reference_analysis.yaml`
- `templates/reference_finding.yaml`
- `templates/reference_evidence.yaml`
- `templates/reference_synthesis.yaml`
- `tests/test_reference_protocol.py`
- `docs/reports/reference-analysis/REFERENCE_ANALYSIS_R1_PROTOCOL_SCHEMA_REPORT.md`

R0 的架构审计和实施计划文件保留不变，作为本轮来源记录。

## Protocol

- v1 首批来源类型为 `web_page`、`image`、`text_description`；未来类型进入 `reserved`，必须通过版本化枚举、Schema 和适配器注册后才能启用。
- 支持 `inspiration`、`adaptation`、`close_recreation` 三种模式；R1 只定义协议，不实现版权判断引擎。
- 12 个分析维度均使用 `include`、`exclude`、`unspecified` 三态范围，明确排除项不会退化成布尔值。
- 来源、分析、Finding、Evidence、Synthesis 通过稳定 ID 和项目相对路径串联。Finding 强制区分 `observed`、`inferred`、`unknown` 与置信度；数字支持 measured、estimated_range、estimated_tolerance，并明确估算不等于实测。
- Evidence 只保存项目内相对 `artifact_ref`、viewport、定位信息和 SHA-256 完整性信息，不把 HTML、图片或长文本写入状态投影。
- Synthesis 支持多来源，并固定 `adopt`、`adapt`、`avoid` 和 `unknown`。确定性校验保证 `Decision -> Finding -> Evidence -> Reference Source`，且 `Reference Decision != Requirement`。
- 所有引用默认 `trust_level: untrusted`，不得表达 instruction、workflow 或 runtime authority；`system_discovered` 来源没有显式授权时不能进入正式 synthesis。
- R1 采用当前仓库既有的三位数字 ID 约定，例如 `REF-001`、`REFFND-001`、`REFEV-001`、`REFDEC-001`，避免引入另一套编号格式。

## Schemas

新增 6 个 v1 Schema：

1. `reference_source_v1.schema.json`
2. `reference_scope_v1.schema.json`
3. `reference_analysis_v1.schema.json`
4. `reference_finding_v1.schema.json`
5. `reference_evidence_v1.schema.json`
6. `reference_synthesis_v1.schema.json`

Schema 使用版本化枚举、稳定 ID、显式上下文和有限的 `additionalProperties` 边界。Change Request 上下文要求显式绑定 `CR-<4 digits>`，旧 Synthesis 不被修改，只通过 `supersedes` 和新版本路径追加历史。

## Workflow

新增正式状态：

```yaml
REFERENCE_ANALYSIS:
  next_role: null
  active_module: reference_analysis
```

状态可从 Intake/Requirements 进入，也可在分析失败或阻塞后路由到 `WAITING_FOR_USER`/`BLOCKED`；分析完成后才允许回到 `PLANNING`。没有新增 `WAITING_FOR_REFERENCE_REVIEW`，也没有把 `reference_analysis` 放进核心 Agent 列表。

现有配置驱动的 `role_selector` 已能把该状态选择为 `MODULE`，因此 R1 没有重写 F10 Runtime 核心。

## Ownership

- First-Ask Intake 负责登记用户提供的引用事实、`reference_status`、原话和范围，不执行分析。
- `reference_analysis` Module 负责 `reference_analysis_status` 与 `active_reference_synthesis`，只能追加 `memory/references/`、`artifacts/references/` 和 Change Request 引用工件。
- Planner、Generator、Evaluator 保持原有三角色；它们不拥有引用原始历史，也不能把 Reference Decision 当作 Requirement。
- Planner 仍不得修改 `memory/requirements/`，Generator 仍不得修改验收规则，Evaluator 仍不得修改代码或计划。

## Backward compatibility

- 没有升级到 schema v8；v7 继续通过 v6 业务 Schema 加 Runtime 扩展。
- 三个新投影字段为可选字段；没有引用的既有 v7 项目不需要补写引用数据，仍可通过状态校验。
- `project.yaml` 只保存最小投影：`reference_status`、`reference_analysis_status`、`active_reference_synthesis`。原始来源、分析、证据和二进制内容不进入 `project.yaml`。
- 追加式文件路径使用 `memory/references/reference-<nnn>/`、`memory/references/synthesis/`、`artifacts/references/` 和 Change Request 专属目录，不覆盖旧版本。

## Tests

已执行：

- `python -m pytest -q tests/test_reference_protocol.py` → **15 passed**
- `python -m pytest -q` → **623 passed, 5 skipped, 113 subtests passed**
- 6 个引用 Schema 均成功解析为 JSON。
- `git diff --check` → **通过**

R1 测试覆盖来源类型、非法类型、三态范围、显式排除、认识论状态、估算值、Evidence 完整性、来源链、悬空引用、追加版本、Workflow 路由、三 Agent 约束、无引用 v7 兼容、不可信权限字段和 Change Request 绑定。

## Known limitations

- 尚未实现 Web/Image/Vision/Browser/Repository/PDF/Video Adapter、Capture、Analyzer、Synthesis Engine 或 Reference Evaluator Gate。
- `runtime/orchestrator.py` 当前的通用 Module CAS 提交入口仍只允许既有 `change_request`；R1 不绕过 Lease/CAS，也不扩展 F10 Module Bridge。该项是 R2 的明确运行时工作。
- Planner Prompt、Context Policy 和真实 First-Ask 运行时注册逻辑留到后续阶段；R1 先提供可执行的协议、模板与状态契约。
- R1 不判断版权、品牌合规或“是否过度复刻”，也不把引用自动转成产品需求。

## R2 readiness

READY_WITH_LIMITATIONS。协议和 Schema 已具备进入 R2 的基础，但 R2 必须先设计通用 Module Runtime Bridge，确保 `reference_analysis` 的状态提交仍经过 Worker Lease、expected revision 和 CAS；随后才能接入受控 Adapter。

## Recommendation

START R2 after explicit user authorization. 本轮 R1 到此停止，不自动启动 R2。
