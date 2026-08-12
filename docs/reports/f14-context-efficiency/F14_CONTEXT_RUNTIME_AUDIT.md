# F14 Context Runtime 审计报告

审计阶段：F14-A — Audit Only  
审计日期：2026-08-11  
审计范围：当前 Skill 本体仓库 C:\Users\28388\Desktop\ai-development-team-skill  
审计目标：判断现有 Runtime、Context、Invocation、证据与测试基础，确认 F14 后续改造的最小安全边界。

## 1. 结论摘要

当前仓库已经有一套可工作的 F10–F13 Runtime 基础：project.yaml 作为项目状态来源，Session Store 保存运行时事件和调用生命周期，Project State CAS/Lease 保护并发写入，F13 Context Builder 提供角色隔离、来源优先级、字节预算、哈希清单和增量恢复，E1 保证 Evaluator 使用独立的新调用与受限上下文，F9 及 Browser Harness 提供结构化证据和强制回归边界。

但是，F14 目标组件目前基本不存在。源码、配置、Prompt 和测试中没有发现可用的 Artifact Index、Dependency Graph、Context Coverage Gate、Context Escalation、Invocation Gate、统一效率 Telemetry、Canonical Summary Cache 或 Project Effort Profile。现状是“角色级来源集合 + 字节预算 + 手工追加引用 + 每次调用前重建 Context”，还不是“任务级依赖图驱动的分层上下文”。

本次审计不对 F14 做 PASS/FAIL 判定，也没有修改生产代码。结论是：可以进入 F14-B 的设计评审，但不应在没有覆盖门、来源授权和回归证据前，直接减少模型调用、合并角色上下文或以摘要替换已批准原文。

当前最重要的判断如下：

1. 最大的 Context/Invocation 浪费，是每个角色运行都按角色固定来源集合重新读取、解码、扫描和组装一批可能与当前任务无关的文件；恢复模式仍会先完整重建当前候选来源，再做差异比较。
2. 最大的质量风险，是没有语义级 Context Coverage Gate。尤其 Generator 和 Evaluator 的正式 Context 配置没有直接纳入 active_requirements 全文，存在只凭 approved_plan、handoff 或 reference binding 运行而漏掉用户硬约束的风险。
3. 最值得优先 Python 化的是确定性状态和证据工作：Artifact/版本定位、显式依赖图、变更文件索引、测试结果解析、来源哈希缓存，以及 Change Request 中无需真实模型调用却创建 Invocation 记录的路径。
4. Selective Context 最合适的落点是现有 F13 Context Builder 的来源收集和选择之间：先由 Python 生成任务依赖与 Mandatory/Task Relevant/On Demand 候选，再由 Coverage Gate 验证，最后才交给模型。
5. 不应优化掉 Planner/Generator/Evaluator 的角色隔离、E1 独立评估、用户确认门、批准链、当前 revision/Lease/CAS、Mandatory Regression、证据可追溯性、路径/能力/Secret 安全边界和恢复幂等性。
6. F14-B 的最小安全范围应只包含确定性索引与解析基础：Artifact Index、显式 Dependency Graph、Diff Index、Test Output Parser、来源哈希缓存和对应测试/报告；暂不减少 Invocation，暂不做摘要替换，暂不引入自动语义猜测。

## 2. 审计方法与证据等级

本报告把同一能力按“说了什么”和“实际能阻止什么”分开记录：

| 证据等级 | 含义 | 本次使用的证据 |
| --- | --- | --- |
| Documented | 文档描述的架构或约束，不代表运行时必然执行 | docs/RUNTIME_CAPABILITY_STATUS.md、docs/MANAGED_RUNTIME_ARCHITECTURE.md、F13 报告 |
| Prompt-enforced | 通过角色 Prompt 要求模型遵守，模型可以误判或漏做 | prompts/planner_prompt.md、prompts/generator_prompt.md、prompts/evaluator_prompt.md |
| Runtime-enforced | Python Runtime 在执行路径上检查、拒绝、记录或提交 | runtime/context、runtime/phase_runner.py、runtime/session_store.py、runtime/verifiers.py 等 |
| Test-proven | 有当前可重现测试或明确的历史测试报告支持 | tests/test_context_*.py、tests/test_phase_runner.py、tests/test_evaluator_independence.py、F13 历史报告 |

“Prompt 写了”不等于“Runtime 强制”；“Runtime 做了字节预算”也不等于“已经完成任务级覆盖验证”。

### 当前测试执行限制

本次尝试执行了重点回归集合：

    python -m unittest -v tests.test_context_builder tests.test_context_budget tests.test_context_resume tests.test_formal_context_builder tests.test_context_rollover tests.test_role_thread_isolation tests.test_evaluator_independence tests.test_evaluation_governance tests.test_evaluation_evidence tests.test_evaluation_protocol

结果为 120 个测试启动，29 个环境错误。错误集中在 tempfile.TemporaryDirectory/pytest 临时目录无法找到可写目录（C:\Users\28388\AppData\Local\Temp 等），不是已经定位到的 F14 产品断言失败。因此，本报告把源码和现有测试作为能力证据，但不把这次运行视为完整的当前回归证明。F13 历史报告中的测试数字也只作为历史证据，不替代本次可复现测试。

## 3. 当前 Runtime 轮廓

### 3.1 已存在且应保留的基础

| 能力 | 当前实现 | 证据等级 | F14 判断 |
| --- | --- | --- | --- |
| 项目状态与 revision | runtime/project_revision.py 使用 Lease、revision、状态哈希和 Compare-And-Swap | Runtime-enforced；相关测试 | F14 的索引必须是派生数据，不能成为第二状态源 |
| Session/Event 持久化 | runtime/session_store.py 保存 Session、Role Run、Invocation、事件和恢复状态 | Runtime-enforced | F14 Telemetry 应挂接现有生命周期，不应绕过 Store |
| Role 隔离 | runtime/role_execution.py 与 config/role_execution.yaml 支持 fresh invocation、等待态不启动角色 | Runtime-enforced + Test-proven | 不能为了复用 Context 合并角色会话 |
| E1 独立评估 | runtime/evaluator_independence.py 与 config/evaluation_independence.yaml 要求新调用、独立上下文和当前 revision | Runtime-enforced + Test-proven | 是质量边界，不是效率优化项 |
| F13 Context Builder | runtime/context/builder.py 提供角色范围、来源优先级、字节预算、来源 hash、manifest 和 resume | Runtime-enforced + F13 文档/测试 | F14 应在其上增加任务相关性和覆盖门 |
| 证据与回归 | runtime/verifiers.py、runtime/browser、scripts/evaluation_evidence.py、F9 配置 | Runtime-enforced + Test-proven | 不应以“少读证据”为理由削弱强制回归 |
| 用户批准链 | Planner Prompt、approval 脚本和变更请求流程 | Prompt + Runtime + Documented | F14 只能提高信息供给效率，不能绕过确认 |

### 3.2 F14 目标组件盘点

| F14 组件 | 当前状态 | 结论 |
| --- | --- | --- |
| Artifact Index | 未找到 ArtifactIndex 或等价统一索引 | 只有散落的 state reference、目录扫描和脚本逻辑 |
| Dependency Graph | 未找到 DependencyGraph 或等价任务依赖图 | 当前只有 ID 提取、来源引用和手工追加引用 |
| Mandatory / Task Relevant / On Demand | 未作为 Context 数据模型存在 | 当前主要是 REQUIRED/HIGH/NORMAL/REFERENCE_ONLY 优先级 |
| Context Coverage Gate | 未找到 ContextCoverage 或 coverage_gate | REQUIRED 来源选择不是语义覆盖证明 |
| Context Escalation | 未找到结构化 context_request 协议 | 扩展引用由调用方通过 additional_references 手工提供 |
| L1/L2/L3 Context | 未找到分层 Context 模型 | 现有 rollover 是阈值切换，不是任务相关分层 |
| Source Hash Cache | 未找到来源读取缓存 | read_bytes() 后每次重新解码、扫描并算 hash |
| Canonical Summary Cache | 未找到稳定摘要缓存 | F13 manifest 只保存来源元数据和 hash，不保存摘要 |
| Test Output Compaction | 有安全截断和调用方提供的 test_metrics | 没有从原始 pytest/JUnit 输出生成统一 summary 的 Parser |
| Browser Evidence | 已有结构化 scenario/action/console/network/screenshot 证据 | 是部分 F14 基础，但还不是通用 Context 层 |
| Invocation Gate | 未找到 InvocationGate | PhaseRunner 对正式 Role Run 会创建并调用模型 |
| Efficiency Telemetry | 未找到统一 token/context/cache/expansion 指标 | BenchmarkResult 有 token_usage 字段，但不是 Runtime 采集 |
| Project Effort Profile | 未找到 F14 字段模型 | 当前有 simple/standard/deep 和 Harness Policy，维度不足且正式路径默认 FULL |

## 4. Model Invocation 清单

### 4.1 正式角色调用

| 路径 | 触发条件 | 当前行为 | 重复/缓存判断 | 不能删除的质量边界 |
| --- | --- | --- | --- | --- |
| runtime/phase_runner.py:385-689 | Planner、Generator 或 Evaluator 的正式 Role Run | 预检后构建 Context，调用 store.create_model_invocation()，再通过 RoleExecutionBroker.invoke() 调模型；要求步骤、Verifier 和 Attestation | 只对同一 run/idempotency key 做生命周期幂等；没有语义输入 hash、结果缓存或 Python-only gate | Generator 预检、Evaluator Runtime Verifier、Attestation、CAS |
| runtime/role_execution.py:255-437 | 正式角色执行模式选择 | 根据 capability 选择 child/fresh，必要时 fallback 到 fresh；Evaluator 要求 fresh | 这是隔离策略，不是调用复用策略 | Role isolation、E1 fresh context、等待态不启动 |
| runtime/context/rollover.py:156-276 | 工具调用、压缩次数、耗时、phase/role transition 达到阈值 | 写入 handoff，创建 fresh invocation，并重建 Context | 只按阈值和 handoff 做 rollover；没有 Context Coverage 或输入缓存 | rollover 的可恢复性、handoff 校验、fresh Context |

ModelInvocationRequest（runtime/phase_runner.py:61-80）已经携带 role、phase、context manifest、revision、capability、fresh_context、isolation 等安全信息，但没有 invocation reason、输入依赖 hash、估算/实际 token、cache hit/miss、coverage 或 expansion 记录。SessionStore 的 model_invocations（约 runtime/session_store.py:243-263、1725-1860）同样只记录调用生命周期和结果 hash，不记录 F14 效率维度。

### 4.2 确定性或专用语义调用

| 路径 | 是否真实调用模型 | 当前判断 |
| --- | --- | --- |
| scripts/change_request.py:245-291 | 否；完成 Runtime transition/verifier、创建 Attestation 和 Invocation 记录，没有调用 model_adapter | 最明确的 Python-only 候选。F14-A 只记录，不在此轮删除或改写 Invocation 语义 |
| runtime/reference_analysis/perception.py:686-762、1036-1152 | 是；通过临时 host thread 执行多模态参考感知 | 这是图像/参考语义理解，不应以通用缓存替代；已有内存 cache、append-only run store、idempotency 和 max invocation，应纳入统一观测 |
| runtime/verifiers.py:193-348 | 否；确定性校验 Runtime transition、来源、测试证据、E1 和浏览器/证据引用 | 应继续 Python 化、扩展为 Coverage/证据完整性基础，不能交给模型判断 |

这里区分了“产生一条 Invocation 记录”和“真的产生一次 LLM 调用”。两者在 Change Request 路径上不是同一件事，后续应通过明确的 invocation reason/type 处理，而不是凭统计数字猜测。

## 5. 重复工作与 Python 化机会

| 工作 | 当前证据 | 性质 | F14 建议 |
| --- | --- | --- | --- |
| 按角色扫描和读取来源 | runtime/context/builder.py:467-589、816-850；每次读取都 read_bytes()、UTF-8 解码、Secret 扫描、计算 hash | 确定性 | 建立可失效的来源读取/hash cache；缓存命中仍校验 revision/path policy |
| Resume 前重新建立当前候选集 | runtime/context/builder.py:251-276 先 self.build(request)，再与旧 manifest 比较 | 确定性 | 先利用来源索引和变化摘要确定候选，必要时再读取；不得跳过 freshness 校验 |
| 找最新 Artifact、版本和感知结果 | PerceptionRunStore.next_run_id/find_valid（runtime/reference_analysis/perception.py:348-554）扫描目录并逐个比较 | 确定性 | 先做统一派生 Artifact Index；保留 append-only 原始记录，索引不是状态源 |
| 批准链、来源绑定和 ID 提取 | runtime/phase_runner.py:182-340、scripts/approval.py 已做多项确定性预检 | 确定性 | 统一显式依赖图和来源 hash，避免每个调用路径各自解析 |
| 变更文件与受影响 AC/测试定位 | Evaluation/Change Request 消费 changed files 和 evidence，但没有统一 Diff Index | 确定性 + 显式映射 | 由 Python 根据批准基线、当前 revision、文件引用生成 diff；不根据文件名猜 AC |
| Test Output 压缩 | scripts/evaluation_evidence.py:573-708 做脱敏和字节截断，GATE-TESTS 依赖调用方提供 test_metrics | 确定性 | 解析 JUnit/pytest/结构化命令结果，保存短 summary、完整日志 locator 和 parser version |
| Browser Evidence | runtime/browser/harness.py 和 evidence.py 已记录动作、observed、console、network、screenshot 并做 gate | 确定性 | 保留当前证据；只增加 Context 摘要/按需 locator，不重新让模型复述浏览器结果 |
| 需求、冲突、产品判断和设计决策 | Planner/First-Ask Prompt 和 discovery/reference 逻辑涉及用户意图、歧义和语义权衡 | 语义 | 保留 LLM；Python 只做事实、来源、状态和覆盖验证 |
| 任务相关性和“哪些证据足够” | 当前没有显式任务图或 coverage model | 混合 | Python 先基于显式依赖生成候选和硬门；模型只能补充语义判断，未知必须扩展或阻塞 |

## 6. Planner / Generator / Evaluator Context 审计

### 6.1 现有配置的共同特点

config/context.yaml 目前以角色预算和静态来源优先级为主。Planner 的最大 Context 为 262144 bytes，Generator 为 393216 bytes，Evaluator 为 327680 bytes；ContextSource.size 是字节大小，不是实际 token 用量。当前没有 context token 估算、真实 token 计数、来源层级、任务依赖、覆盖结果或扩展次数字段。

F13 的 REQUIRED/HIGH/NORMAL/REFERENCE_ONLY 是来源选择优先级，不是“本任务的强制事实集合”。在预算不足时，非 REQUIRED 来源可能被降级或省略；这在文件层面是确定性的，但没有语义层证明“被省略内容与当前任务无关”。

### 6.2 角色分层审计

| 角色 | Mandatory Context 候选 | Task Relevant 候选 | On Demand 候选 | 不能省略的质量边界 | 当前风险 |
| --- | --- | --- | --- | --- | --- |
| Planner | project.yaml、active_requirements、用户明确约束、当前批准阶段、适用的设计/产品确认状态 | 当前需求/AC、研究必要性、coverage/gap、当前产品方案、用户反馈和设计选择 | 历史 proposal、旧评估、完整参考原文、旧轮次设计预览 | 用户权威、First-Ask 事实、批准链、设计/产品确认门 | 来源集合偏宽，任务无关历史和研究材料可能占用 inline budget；没有 coverage gate |
| Generator | project.yaml、approved_plan、产品规格/AC、批准 reference binding、当前 revision、变更范围、安全/兼容约束、可执行测试契约 | 当前 issue → AC → requirement → files → tests 的显式链；受影响代码、测试、最近一次相关 handoff | 旧评估全文、旧设计预览、完整浏览器原始日志、非相关参考资料 | approved_plan、用户硬约束、变更批准、当前代码/测试边界、Security/Path/Capability | config/context.yaml 没有把 active_requirements 作为 Generator 正式普通来源；Prompt 要求读取，但 Runtime Context 不直接保证全文到场 |
| Evaluator | project.yaml、approved_plan、产品规格/AC、evaluation profile、当前 revision/code snapshot、Mandatory Regression、E1 独立性契约 | 当前 issue package、changed files、受影响 AC、测试/浏览器/回归证据和失败重现材料 | 旧评估、历史 handoff、完整 raw logs、非相关参考原文 | approved scope、当前 revision、独立 Context、证据原件 locator、Mandatory Regression、保护文件 | active_requirements 也未直接进入 Evaluator Context；依赖 approved_plan/hand-off 的闭包可能漏掉原始用户约束 |

Generator Prompt（prompts/generator_prompt.md:91-110）明确要求读取 active_requirements、批准文件、代码和测试，但当前角色 Context 配置并未直接将 active_requirements 纳入 Generator 的常规来源。runtime/phase_runner.py:182-263 的 generator preflight 会读取并校验部分受保护文件，但这不等于把完整 requirement 内容交给模型，也不等于 Coverage Gate。Evaluator 有类似的“规格/计划/证据依赖闭包”风险。

### 6.3 最重要的 Context 质量问题

1. **没有 Mandatory Context 的语义模型。** 当前 REQUIRED 只是配置优先级；没有记录“本任务必须覆盖哪些 requirement、AC、批准条件、当前 revision 和安全约束”。
2. **没有任务依赖图。** ContextBuildRequest（runtime/context/models.py:28-47）只有 session、run、role 和手工 additional_references，没有 current task、issue、AC、changed files 或 dependency root。
3. **手工扩展不可审计。** builder.py:552-589 接收 additional_references，但没有结构化 expansion reason、授权结果、覆盖前后差异和拒绝事件。
4. **Resume 只减少交付内容，不减少候选读取。** 旧 manifest 能做 hash/delta 比较，但当前 build 仍会先把候选文件读完。
5. **Context 永久清单与上下文内容分离是好事，但还没有 On Demand locator。** 被省略来源保留 ref/hash/reason（models.py:119-148），这是可恢复性基础；F14 应在此基础上提供安全定位和扩展，而不是直接删除历史。

## 7. Project Effort Profile 审计

当前有两个相关但不等价的机制：

- runtime/requirements_discovery/core.py:141-294 产生 likely_complexity 和 simple/standard/deep 研究预算。
- runtime/harness_policy.py:16-156 根据 model、risk、browser、task complexity、AC 数量和 workflow 数量选择 LEAN/STANDARD/FULL；runtime/orchestrator.py:181-185 正式路径以 evidence_sufficient=False 调用选择，因此当前正式执行偏向 FULL。

这些机制不能直接替代 F14 的 Project Effort Profile。F14 至少需要分别记录：

product_complexity、technical_complexity、data_complexity、integration_complexity、design_ambition、interaction_ambition、quality_rigor、risk_level、user_certainty，并按 stage 计算 execution_depth。

该 Profile 只能决定工具深度、候选来源范围、验证强度和是否允许缓存候选；不能决定是否跳过批准、E1、Mandatory Regression、Secret/Path/Capability 校验，不能把未知强行降级为 simple。

## 8. 最大浪费、最大风险与边界

### 8.1 最大 Context/Invocation 浪费（按优先级）

1. **固定角色 Context 过宽。** Planner/Generator/Evaluator 以静态来源清单为主，任务无关的历史 handoff、旧评估、研究或参考内容可能进入 inline budget。
2. **Context resume 的重复文件读取。** build_resume() 先完整 build，再比较来源 hash；来源没有读取/hash cache。
3. **确定性 Change Request 仍产生 Invocation 记录。** 该路径没有真实模型调用，是最干净的 Python-only 候选，但必须先设计 Invocation type/reason 和统计兼容。
4. **Artifact/感知运行扫描分散。** Perception run store 和其他状态引用各自定位最新结果，缺少统一派生索引。
5. **原始测试输出与调用方 metrics 分离。** 已有截断和脱敏，但模型仍可能收到过多 raw evidence，且 metrics 不是统一 Parser 生成。
6. **没有输入 hash 和结果 reuse。** 正式 PhaseRunner 不知道“本次任务输入是否与已有已验证结果完全相同”，因此无法安全地区分重复调用和必须重做。

### 8.2 最大质量风险（按优先级）

1. **Mandatory Context 漏项。** Generator/Evaluator 没有直接纳入 active_requirements 的完整内容，且没有 Coverage Gate；这是最高优先级风险候选。
2. **相关性选择错误。** 没有显式依赖图时，Selective Context 若直接交给 LLM 猜，可能漏掉受影响 AC、测试、审批条件或安全约束。
3. **摘要取代原件。** 对已批准 plan、requirements、用户禁止事项、产品规格只保留摘要，会丢失权威措辞和可追溯性。
4. **缓存跨角色复用。** 如果复用 Generator 的 Context、历史 handoff 或结果，会破坏 E1 的独立性和 Evaluator 的反证能力。
5. **索引变成第二状态源。** 如果 Artifact Index 可以覆盖或推断 project.yaml，会破坏 F10 的 CAS/Lease 和恢复模型。
6. **扩展路径不受控。** 任意文件路径、未授权 role、未验证 revision 的 Context expansion 可能造成越权、Secret 泄露或 stale evidence。

### 8.3 不能优化掉的质量边界

- 用户确认、产品方案确认、Plan 批准和变更批准。
- Planner、Generator、Evaluator 的角色职责与运行隔离。
- Evaluator 的 fresh invocation、独立 Context、当前 revision 和不继承 Generator 声明。
- project.yaml、Session Store、Lease、revision、CAS、恢复幂等和 append-only 历史。
- Mandatory Regression、受保护文件快照、证据 locator、Runtime Verifier 和 Browser Gate。
- Path/Capability/Secret 扫描、命令安全、workspace 边界和 managed project 隔离。
- “原始权威内容可定位、摘要可验证、被省略内容可恢复”。
- 不确定时返回 unknown、申请 Context expansion 或阻塞，不能用模型猜测填空。

## 9. F14-A 组件状态与证据矩阵

| 能力 | Documented | Prompt-enforced | Runtime-enforced | Test-proven | 当前审计结论 |
| --- | --- | --- | --- | --- | --- |
| F13 来源优先级/字节预算 | F13 报告 | 角色 Prompt 规定读取边界 | runtime/context/builder.py:742-814 | tests/test_context_budget.py、tests/test_formal_context_builder.py | 已有，但不是任务 Coverage |
| F13 manifest/resume | F13 增量报告 | Prompt 可引用最近 handoff | builder.py:251-376 | tests/test_context_resume.py | 已有；resume 前读取重复仍在 |
| Role/E1 隔离 | 架构/F9/E1 文档 | evaluator_prompt.md:47-71 | role_execution、evaluator_independence | tests/test_role_thread_isolation.py、tests/test_evaluator_independence.py | 已有且必须保护 |
| Artifact Index | 未见 | 未见 | 未见 | 未见 | F14-B 候选 |
| Dependency Graph | 未见 | 仅有来源/ID 约束 | 未见统一图 | 未见 | F14-B 候选 |
| Coverage Gate | 未见 | Prompt 要求读取相关文件 | 未见 | 未见 | F14-C 必须补齐 |
| Context Escalation | 未见 | 无结构化协议 | 只有手工 additional_references | 未见 | F14-D 必须补齐 |
| Invocation Gate | 未见 | 无 F14 协议 | 未见；正式 Role Run 直接调用 | 未见 | F14-F 才能处理 |
| Test Output Parser | 有证据/metrics 文档 | Evaluator 要求证据 | 截断、脱敏、消费 metrics | 有 evidence gate 测试，但无 parser 测试 | F14-B 候选 |
| Efficiency Telemetry | Benchmark 文档中有结果字段 | 无 | 无统一采集 | 无 | F14-G 候选 |
| Effort Profile | 无 F14 版本 | 有简单复杂度提示 | 有 discovery/harness 局部机制 | 无 F14 测试 | F14-C/F14-F 后置 |

## 10. F14-A 明确输出

### 当前最大的 Context/Invocation 浪费

固定角色来源清单造成的宽 Context、resume 前重复读取、缺少来源 hash cache，以及确定性 Change Request 路径产生的非真实 Invocation 记录，构成当前最大浪费组合。

### 当前最大的质量风险

没有按任务建立 Mandatory Context 和 Coverage Gate，导致 Generator/Evaluator 可能在拿到 approved_plan 的情况下没有拿到完整 active requirements 或受影响 AC/安全约束。这个风险在引入 Selective Context 前必须先解决。

### 最值得 Python 化的部分

Artifact/版本定位、显式依赖关系、approved baseline 到当前 revision 的 Diff、测试结果结构化解析、来源读取/hash cache，以及 Change Request 的确定性过渡记录。LLM 仍负责意图、歧义、产品/设计决策和复杂语义质量判断。

### Selective Context 的最佳落点

放在 runtime/context/builder.py 的 _collect_sources() 与 _select_sources() 之间，前面增加 Python 维护的 Artifact Index、Dependency Graph 和任务输入；后面增加 Coverage Gate。模型调用前必须生成可审计的 manifest，扩展只接受结构化、授权、受预算和次数限制的请求。

### 最小安全 F14-B 范围

只实现并测试以下确定性基础：

- 从现有状态引用、批准工件、evidence locator 和文件快照生成派生 Artifact Index。
- 从明确的 requirement/AC/issue/file/test/reference ID 生成 Dependency Graph；无法确定的边标记 unknown，不猜测。
- 生成 current revision 对 approved baseline 的 Diff Index。
- 解析结构化测试结果/pytest 或 JUnit 结果，保留原始日志 locator、脱敏规则和 parser version。
- 为来源读取和 hash 建立 revision/path-policy-aware cache。
- 建立故障注入测试和审计指标的最小数据模型。

F14-B 不应包含 Invocation 减少、跨角色缓存、摘要替换、自动语义相关性猜测、跳过用户批准、修改 E1 或修改 project.yaml 状态模型。

## 11. 进入 F14-B 前的决策门

F14-B 开始前应明确批准以下不变量：

1. project.yaml 仍是项目状态唯一来源；所有 Index/Graph/Cache 都是可重建派生物。
2. approved 原文、用户禁止事项和证据原件始终可定位；summary 不得替代权威原文。
3. F14-B 不改变正式 Invocation 次数和 Role 隔离。
4. 所有缓存 key 至少包含来源身份、内容 hash、project revision、policy hash、role scope 和 parser/version 信息。
5. unknown 只能触发扩展、降级到更强验证或阻塞，不能隐式当作“无影响”。
6. 测试环境必须先提供可写临时目录，才能把回归结果标为当前 Test-proven。

## 12. F14-A Plan Hardening 追加审计（2026-08-11）

本节是对本报告的追加修订，不覆盖前面的历史审计结论。它记录 Go/No-Go Hardening 后的当前测试证据和已经定稿的底层语义。

### 12.1 当前测试基线

在 Skill 仓库之外创建了专用目录：

    C:\Users\28388\Desktop\f14-a-temp-tests-20260811

测试进程显式设置了 TEMP、TMP、TMPDIR，并执行了独立的 tempfile.TemporaryDirectory 创建/清理验证：

- tempfile.gettempdir() 指向上述目录。
- TemporaryDirectory 创建成功。
- 显式 cleanup 后目录不存在。

随后在同一环境下执行了原 F14-A 重点回归集合：

- 120/120 通过。
- 0 error、0 failure。
- 运行时间 1.332 秒。

又执行了 tests 目录完整集合：

- 421/421 通过。
- 0 error、0 failure。
- 运行时间 26.975 秒。

证据分类：

| 观察 | 分类 | 说明 |
| --- | --- | --- |
| 前一轮 29 个错误 | Environment Failure | Python 在默认 TEMP 候选目录中找不到可用临时目录；切换到 Skill 仓库外的显式可写目录后恢复 |
| 本次重点集合结果 | Current Run Verified | 120/120 OK |
| 本次完整集合结果 | Current Run Verified | 421/421 OK |
| Existing Product Failure | 未发现 | 当前完整集合无失败断言 |
| F14-related Failure | 未发现 | 本轮没有 F14-B 生产代码，当前集合没有新增 F14 失败 |
| Unknown | 未发现 | 当前可观察错误已由环境复验完成分类 |

F13 历史报告仍只属于 Historical Test-Proven；本次 Go/No-Go 使用的是上述 Current Run Verified 结果。

### 12.2 三个独立效率指标域

F14 后续必须把文件/解析运行成本、Context 交付成本和真实模型成本分开计量：

~~~yaml
runtime_efficiency:
  file_reads:
  bytes_read:
  hash_operations:
  directory_scans:
  parser_runs:
  runtime_latency:

context_efficiency:
  context_source_count:
  context_bytes:
  duplicated_context_bytes:
  mandatory_bytes:
  task_relevant_bytes:
  on_demand_bytes:
  omitted_bytes:

model_efficiency:
  llm_invocations:
  input_tokens:
  cached_input_tokens:
  output_tokens:
  model_latency:
~~~

file_reads/hash_operations/directory_scans 减少，只能说明 Runtime Efficiency 改善，不能直接宣称 Token Efficiency 改善。Host 没有提供真实 token usage 时，model_efficiency 的 token 字段必须标记为 estimated 或 unavailable；字节数不得冒充 token。

Change Request 的 Invocation lifecycle record 也不再被当成真实模型调用。Telemetry 必须记录 execution_type，并重点统计 Real LLM Invocation Count。

### 12.3 Source Hash Cache 最终语义

F14-B 采用两层缓存：

**Layer 1 — Source Fingerprint / Locator Cache**

记录 canonical locator、file identity、size、mtime/filesystem metadata、project revision、policy hash、role scope、parser version 和 previously known content hash。它只用于减少目录定位和判断是否需要读取，不能作为内容真实性的最终证明。

**Layer 2 — Content Addressed Cache**

以可信 content_hash 为核心，分层保存 decoded content、parsed representation 和 deterministic summary/index result。原始解码内容可以按 content hash 共享；解析结果仍必须绑定 parser/version、policy 和适用 role scope，不能把受 E1 约束的语义结果跨角色复用。

失效与回退规则：

1. canonical locator、file identity、路径授权或 workspace 边界变化时，Layer 1 失效。
2. file size、mtime 或其他 metadata 变化时，只能作为变化提示；不能用 mtime + size 替代 content hash。
3. protected artifact、security-sensitive artifact、用户明确约束、approved source、revision/policy 变化或 metadata 可疑时，必须重新读取并计算可信 hash。
4. project revision、policy hash、role scope 或 parser version 不匹配时，相关 parsed/summary entry 失效。
5. 读取到的内容 hash 与 previously known content hash 不一致时，丢弃旧派生结果并重新解析；不得继续使用旧 summary。
6. cache entry 的 schema、checksum、content hash、parsed output hash 或 locator 校验失败时，标记 cache corruption，忽略该 entry 并回退到重新读取；不以损坏缓存作为 authority。
7. fast fingerprint 只允许作为非权威、非安全敏感派生操作的优化提示；凡是要证明批准来源、Mandatory Coverage、Security 或 E1 的操作，必须完成可信内容校验。
8. 文件不存在、无法读取、权限改变、符号链接/重解析点异常或 locator 不可解析时，状态为 stale/unknown，不得假设内容未变。
9. cache 命中不能跳过 Secret scan、path policy、revision freshness 和 role authorization。
10. 失效必须可观察，至少记录 cache hit/miss、invalidation reason、fallback read 和 parser version。

### 12.4 Dependency Graph 最终 Edge Model

F14 不建立一条混合 authority 和 execution 语义的模糊边。逻辑上分为两个图：

**Authority / Provenance Graph**

支持 derived_from、approved_by、supersedes、sourced_from、governed_by。

**Execution / Verification Dependency Graph**

支持 satisfies、implements、affects、verifies、evidenced_by、regresses_with。

如果使用统一 Graph 存储，每条 Edge 至少包含：

~~~yaml
graph_kind:
edge_type:
source:
target:
evidence_ref:
source_hash:
revision:
confidence:
~~~

只有由 approved source、明确 ID、明确 locator、当前 revision 和可验证证据支持的关系，才允许 confidence 为 explicit。无法证明的候选关系必须记录为 unknown 或不建立硬依赖；禁止仅凭文件名、关键词、模块名相似或模型猜测建立执行依赖。

### 12.5 Complete Mandatory Requirement Coverage

Coverage Gate 的目标正式定为 Complete Mandatory Requirement Coverage，而不是 Always Load Entire Requirements Document。

对当前 task、role、revision，Runtime 必须建立 Mandatory Context 集合，并逐项证明：

- 直接相关 Requirements。
- 当前相关 Acceptance Criteria。
- 用户明确硬性要求与明确禁止事项。
- Global、Security、Privacy、Data、Compatibility、Regulatory Constraints。
- Approved Scope。
- 当前 Change Request 范围（如适用）。
- 当前 revision 和 authoritative source binding。

一个 Mandatory 条目只有在以下条件之一成立时才算 COVERED：

1. structured index 提供稳定 ID、authority、source hash、revision 和 exact source locator，且 Context 交付了该 exact authoritative source unit。
2. Runtime 证据明确证明该条目为 NOT_APPLICABLE，并且该结论来自可复现的权威来源/范围规则，不是模型猜测。

仅有 AI Summary 只能作为导航或辅助，不能完成 Mandatory Coverage。每个条目应能表达类似：

~~~yaml
mandatory_context:
  - id: SECURITY-002
    authority: USER_EXPLICIT
    source_ref: requirements_v003
    source_hash: verified-hash
    exact_source_locator: requirements_v003.sections.security.SECURITY-002
    status: COVERED
~~~

Coverage 状态至少包括 COVERED、MISSING、STALE、CONFLICT、UNKNOWN 和 NOT_APPLICABLE。只有所有适用 Mandatory 条目为 COVERED 或有权威证据的 NOT_APPLICABLE，Coverage Gate 才能通过；UNKNOWN 不能默认为无影响。

这意味着 Generator/Evaluator 不必每次加载整份 active_requirements，但必须拿到当前任务所需的 exact source units，并能由 Runtime 证明完整覆盖。

### 12.6 Artifact Index 最终存储位置

Artifact Index、Dependency Graph、Source Cache 和 Diff Index 定义为 F10 Control Plane / Runtime Store 的派生数据。最终落点是现有 schema v7 Session Store 的 derived tables/受控派生数据区；对 managed project 使用项目 .runtime/sessions.sqlite3，若部署由外部 F10 Control Plane 托管，则使用该 Control Plane 的 sessions.sqlite3。Skill 本体仓库不创建 project.yaml，也不创建项目运行时 Store。

这些数据不得写回项目业务目录形成第二状态源，不能替代 project.yaml，必须可以从项目状态、批准工件、Evidence 和当前 revision 重建。索引损坏、缺失或过期只能导致重建、stale/unknown 或阻塞，不能改变业务状态。

### 12.7 Test Output Parser 最终语义

Parser 输入优先级固定为：

1. Existing Structured Runtime Result。
2. JUnit 或其他 machine-readable test result。
3. Structured pytest-compatible result。
4. Human console parsing fallback。

无法可靠判断时必须返回 parse_status: UNKNOWN，不能自动改写为 passed: true。输出必须保留 parser version、source locator、raw log locator、command、exit code 和 parse confidence/status；原始 evidence 不删除。

### 12.8 F14-F 结果复用边界

F14-F 第一版只允许 Deterministic Python Result Reuse：file hash、Artifact Index、Test Parser Result、Diff、Browser deterministic evidence summary 和 protected snapshot verification。

第一版明确禁止 LLM Semantic Result Reuse，包括 Evaluator PASS/FAIL、Generator implementation output、Planner product decision，以及 Generator Context/结果复用给 Evaluator。Evaluator 每轮正式 evaluation 仍必须 fresh invocation、current revision、independent context 和 independent evidence interpretation。

## 13. F14-A Hardening 结论

当前测试基线已经建立，底层语义和 F14-B 的安全边界已定稿，故本次 F14-B Go/No-Go 结论为：

**GO（仅表示允许进入下一次明确授权后的 F14-B 生产实现准备，不表示本轮已授权或已开始实现）。**

F14-B 仍必须等待用户下一次明确授权。本轮没有实现 Artifact Index、Dependency Graph、Coverage Gate、Invocation Gate，也没有减少 Model Invocation。
