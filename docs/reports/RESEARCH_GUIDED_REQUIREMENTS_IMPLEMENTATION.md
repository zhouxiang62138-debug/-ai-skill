# Research-Guided Adaptive Requirement Discovery 实施记录

## 目标

本轮把 Research-Guided Adaptive Requirement Discovery 接入现有 First-Ask Intake、F10 Runtime、Context Policy 和 Planner 边界。Research、Coverage、Gap、Question Priority、Opportunity Map 都是 Module/Service 或追加式工件，不是第四个 Agent。

## 已实现协议

- `Initial Intent Analysis`：把用户原话、候选领域、未知事实、复杂度、风险和研究价值分开保存。
- `Research Gate`：确定性输出 `required`、`optional` 或 `not_required`，并绑定预算、来源层级和隐私约束。
- `Coverage Map` / `Gap Analysis`：覆盖产品目标、用户、平台、工作流、数据、权限、集成、安全、性能、部署和视觉决策等维度。
- `Question Set`：按决策影响、架构影响、工作流影响、不可逆性、依赖数和风险稳定排序，每轮最多 3 个问题；已回答主题不重复提问。
- `Sufficiency Gate`：只允许安全假设、明确答案或可路由的视觉未决项；高风险未知、冲突和用户决策项继续阻塞。
- `Domain Research`：默认适配器明确返回 `unavailable`；注入适配器时按来源 Tier 1–4 写入 Source/Finding/Summary，所有输出 append-only。
- `Opportunity Map`：严格证据后再发散候选；每个候选带 Finding 引用，永远保持 `candidate`，需要 Planner/用户治理后才能进入方案。

## Runtime 集成

新增的 `REQUIREMENT_RESEARCH` 只表达一个不可等待用户的受控研究阶段；它仍由 F10 Orchestrator、Worker Lease、CAS、Revision 和 Module Authorization 管理。研究完成或不可用后返回 `INTAKE`，由 First-Ask 继续动态提问。

新增字段全部是 v6/v7 可选投影字段：

- `requirements_discovery_status`
- `research_status`
- `intent_analysis_ref`
- `research_requirement_ref`
- `active_research_round`
- `coverage_map_ref`
- `gap_analysis_ref`
- `question_set_ref`
- `sufficiency_evaluation_ref`
- `opportunity_map_ref`

v3–v6 旧项目仍按只读/预览迁移路径处理；未修改旧历史工件，也没有创建 Skill 根目录 `project.yaml`。

## Planner 消费边界

Planner Context 读取 Research Summary、Coverage、Gap 和 Opportunity Map，但 Prompt 明确要求区分：

- `Must Have`：用户明确提出或明确确认；
- `Recommended`：研究支持但仍需目标一致性判断；
- `Opportunity`：带证据的候选机会，需用户决策；
- `Deferred`：暂不纳入范围的建议、未知或高风险猜测。

研究事实不会覆盖用户原话、显式排除、已确认需求或 Sufficiency Gate。

## 验证证据

- 新增 Discovery 协议与 Runtime 测试：18 passed。
- Discovery、Reference、Context、Project State 组合回归：62 passed。
- 完整 pytest：802 passed，6 skipped，113 subtests passed。
- `git diff --check`：通过。

## 已知环境限制

本仓库原有 pytest 缓存目录存在权限限制；测试使用受控系统临时目录完成，没有删除或清空该缓存。默认 Research Adapter 不联网，因此没有把不可复现的外部内容伪装成研究事实。
