# F14 Context Runtime 实施计划

计划类型：F14-A 产出的待审核实施计划  
当前状态：待用户确认；本文件不是批准的开发 Plan，也不授权进入 Generator。  
适用范围：C:\Users\28388\Desktop\ai-development-team-skill Skill 本体仓库  
当前阶段约束：本轮只生成审计与计划文档，不修改生产 Runtime、配置、Prompt 或测试代码。

## 1. 目标与优先级

F14 的目标是让 Context 和 Model Invocation 更小、更快、更可复现，同时不牺牲正确性、完整性、可追溯性、隔离性和用户决策权。

固定优先级：

> Correctness > Completeness > Traceability > Safety > Product Quality > Evaluation Reliability > Runtime Efficiency > Token Reduction

因此，F14 的第一目标不是少发几个调用，而是先让 Runtime 知道“本次任务必须覆盖什么、哪些来源与任务有明确关系、哪些内容可以延迟加载、哪些内容绝不能省略”。

## 2. 现状基线

### 已有基础

- project.yaml、Session Store、Lease、revision、CAS 和恢复机制已形成 F10 基础。
- F13 Context Builder 已有角色范围、来源优先级、字节预算、hash、manifest 和 resume。
- Role Execution 与 E1 已有 fresh invocation、角色隔离和独立 Evaluator 约束。
- F9 的 approval、evidence、issue package、Mandatory Regression、protected snapshot 和 Browser Evidence 已有运行时/测试支撑。

### 尚不存在的能力

- 统一 Artifact Index。
- 显式 Dependency Graph。
- Mandatory/Task Relevant/On Demand 三层 Context。
- Context Coverage Gate 和结构化 Context Escalation。
- 安全的 L1/L2/L3 progressive loading。
- Invocation Gate、语义输入 hash 和统一可解释的缓存命中规则。
- Context/Invocation/token/cache/expansion 的 Runtime Telemetry。
- 多维 Project Effort Profile 和 stage-specific execution_depth。

## 3. 目标架构

    Project State / Session Store / Approved Artifacts / Evidence
                             |
                             v
                  Deterministic Artifact Index
                             |
                             v
                 Explicit Dependency Graph + Diff
                             |
                             v
                  Context Candidate Classification
              Mandatory | Task Relevant | On Demand
                             |
                             v
                   Context Coverage Gate
                       | complete
                       v
              Context Builder + Budget Selector
                       |
              +--------+---------+
              |                  |
         L1/L2/L3 Context   Structured Expansion
              |                  |
              +--------+---------+
                       v
                 Invocation Gate
           Python result/cache or LLM call
                       |
                       v
          Structured Result + Runtime Verifier
                       |
                       v
          CAS / Attestation / Evidence / Telemetry

目标架构中，索引、图、差异、覆盖、授权、预算、hash、CAS、Verifier、测试解析和遥测由 Python 负责。LLM 只负责需求意图、歧义澄清、产品/设计选择、复杂架构权衡、实现、诊断和无法用规则替代的语义质量判断。

## 4. 分阶段路线

### F14-B — Deterministic Extraction

目标：先把可以确定性计算的事实提取出来，不减少正式 Model Invocation。

计划新增或扩展的 Runtime 能力：

1. **Artifact Index**
   - 从 project.yaml 的显式状态引用、approved artifacts、handoff/evidence locator、release/current revision 和已有 append-only 目录生成派生索引。
   - 每条索引记录 artifact ref、kind、path/locator、content hash、project revision、policy hash、producer role、approval/authority 信息、freshness 状态。
   - 索引可以重建；不能回写或覆盖 project.yaml，不能决定业务状态。
   - 原始工件保留，索引只做定位和校验。

2. **Explicit Dependency Graph**
   - 支持 requirement → product spec → approved plan → issue/AC → file/test/evidence/reference 的显式边。
   - 支持 source hash、revision、approval record 和 evidence locator 作为边的证据。
   - 无法从明确引用证明的关系标记 unknown；不根据文件名、关键词或模型猜测建立硬依赖。

3. **Diff Index**
   - 基于 approved baseline、current revision、protected snapshot 和变更请求生成 changed files、changed artifacts、受影响显式节点。
   - 变更不明确时保守扩大候选或交给 Coverage/Expansion，不标记为无影响。

4. **Test Output Parser**
   - 优先消费已有结构化命令结果、JUnit XML、pytest 结果和 test_metrics。
   - 生成短 summary：exit code、passed/failed/skipped/error、duration、failed test IDs、coverage metrics（若有）、parser version、原始日志 locator。
   - 原始输出仍保留脱敏 locator，不把长日志直接复制到每次 Context。

5. **Source Hash Cache**
   - 以 path/locator、content hash、project revision、policy hash、parser/version 和 role scope 形成安全 key。
   - 缓存只减少重复读取/解析；命中前仍需做路径授权、freshness 和 Secret policy 校验。
   - 不跨 role 共享受 E1 限制的语义结果。

F14-B 的验收条件：

- Index/Graph/Diff/Parser 的输出可重建、可 hash、可定位原件。
- 不修改现有 project.yaml 状态协议、Lease、CAS、Role isolation、E1 和 Mandatory Regression。
- source cache 命中和失效有单元测试。
- unknown、stale、缺失 locator、非法路径、revision 不匹配都有负向测试。
- 正式 PhaseRunner 的模型调用次数和调用语义不变。

### F14-C — Context Architecture

目标：在 F13 Context Builder 上增加任务级 Context 语义，而不是另起一套并行 Context。

计划：

- 扩展 ContextBuildRequest，增加可验证的 task/issue、requirement/AC roots、changed files、current revision 和 expansion policy；缺失时使用显式 unknown，不从聊天记录隐式推断。
- 扩展 Context manifest，记录 Mandatory、Task Relevant、On Demand、Omitted、Unknown、Coverage Result、policy/hash/revision。
- 把“必须到场”的来源从静态 REQUIRED 升级为由权限、批准链、任务图和角色规则共同确定的 Mandatory 集合。
- 将 approved requirements、approved plan、用户禁止事项、当前 revision、变更范围、安全约束和 E1 边界设为可验证的硬覆盖项。
- 保持 F13 的 byte budget、no silent truncation、secret scan、path policy、durable manifest 和 resume 语义。

验收条件：

- 在模型调用前能明确回答每个 Mandatory 节点的来源、hash、revision 和 delivery mode。
- coverage 不完整时不得继续进入模型调用。
- Generator/Evaluator 不能仅因为 approved_plan 存在就默认 requirements 已覆盖。
- Evaluator 继续使用独立 fresh Context，不能读取 Generator 的非证据型历史。

### F14-D — Context Escalation

目标：让模型可以申请缺失上下文，但不能自由读文件、猜依赖或突破角色/路径边界。

计划：

- 定义结构化 context_request：request id、reason、missing dependency refs、desired source kinds、urgency、role、revision、current manifest hash。
- Runtime 只允许从 Artifact Index/Dependency Graph 中解析 locator；不接受任意未授权路径。
- 对每次扩展做 role、capability、path policy、Secret scan、revision freshness、budget、max expansion count 和 audit event 校验。
- 扩展成功后重算 Coverage；扩展失败返回结构化拒绝或阻塞原因。
- 将 L1/L2/L3 定义为“最小事实层、任务证据层、受控原文/历史层”，不是简单按文件大小切片。

验收条件：

- 请求、授权、交付、拒绝和覆盖变化均可追溯。
- 非法路径、跨 role、stale revision、Secret、超预算和循环扩展均被拒绝。
- 未知依赖不会静默转成“无需加载”。

### F14-E — Incremental Context

目标：在安全的依赖/索引基础上减少无变化来源的重复构建。

计划：

- 使用 source hash、dependency graph、project revision 和 policy hash 做部分失效。
- Resume 先利用索引和变化摘要筛选候选，再读取必要来源；不能绕过 freshness 校验。
- 对稳定、可验证、非权威替代物的内容提供 Canonical Summary Cache，并保存原文 locator、input hash、generator/parser version 和 policy hash。
- approved requirements、用户约束、approved plan、产品规格、批准记录等权威内容只允许压缩交付形式，不允许摘要替换原件。
- 允许缓存确定性测试 summary、browser evidence summary、diff summary；语义推理结果需要独立的可验证输入和过期规则。

验收条件：

- source mutation、revision change、policy change、role change、parser change 和 dependency change 都能正确失效。
- cache hit 不会减少 Mandatory Context，也不会改变 E1 的独立性。
- 原文可恢复、summary 可验证、omitted 内容可定位。

### F14-F — Invocation Gate

目标：最后才决定“是否需要真实 LLM 调用”，并保留可解释原因。

计划：

- 在 PhaseRunner 创建正式 Invocation 前运行 Python preflight：状态、来源、Coverage、任务依赖、输入 hash、缓存资格、effort profile、risk 和 retry policy。
- 定义 invocation reason/type，例如 semantic decision、implementation、evaluation、deterministic transition、reference perception。
- 对确定性结果先走 Python；对完全相同且仍 fresh、授权、输入 hash、policy 和 role scope 都匹配的结果，才可考虑 reuse。
- Evaluator 的结果不得复用 Generator 结果；Evaluator 必须 fresh invocation，且只能使用独立证据和当前 revision。
- 缓存 miss、unknown、stale、coverage incomplete、风险提升或用户要求新判断时，必须重新调用或升级验证。
- 保留 Invocation 记录，但明确区分 python_only、llm、reused_verified_result、rollover 和 perception，避免把记录数误当成模型调用数。

验收条件：

- 每次“不调用”都有 Python 可重现理由。
- 每次“调用”都有 role、phase、reason、input hash、context manifest、revision、coverage 和 budget 记录。
- F14 不能降低 E1、Mandatory Regression、用户批准或安全门的强度。

### F14-G — Telemetry

目标：采集真实效率与质量数据，避免凭感觉优化。

计划指标：

- invocation：role、phase、reason、python_only/llm/reuse、duration、retry、rollover、success/failure。
- context：manifest hash、revision、source count/bytes、Mandatory/Task Relevant/On Demand/Omitted bytes、coverage、expansion count、cache hit/miss。
- parsing/index：parser version、index rebuild、hash cache hit/miss、invalidations、stale/unknown。
- model：实际 token usage（若 Host 提供），否则明确标记 estimated，不把字节数冒充 token。
- quality：required step pass、evidence completeness、AC coverage、false-pass/critical-miss、browser coverage、regression outcome。

数据约束：

- Event Payload 不保存凭据、原始长模型输出或疑似 Secret，只保存受控 locator、hash、计数和摘要。
- 指标写入现有 Session/Event 或受控派生 Telemetry，不创建第二个不可恢复的状态源。
- 记录 telemetry schema version，支持重放和兼容升级。

### F14-H — A/B Benchmark

目标：证明 F14 同时改善效率和质量，而不是只减少 token。

基线设计：

- 使用现有 scripts/benchmark_runner.py 的结果模型，扩展 context/invocation/cache/coverage 指标。
- 测试项目必须使用 test_ 前缀，位于 Skill 目录之外；完成后进入 archive/，并保留 TEST_REPORT.md。
- 对同一测试项目、同一 revision、同一 approved inputs、同一模型和同一测试命令，比较 baseline 与 F14。
- 至少覆盖：简单低风险任务、跨文件标准任务、带 browser/参考图/重做的复杂任务。

必须观察：

- correctness、completeness、traceability、security、evaluation reliability。
- mandatory context omission、AC coverage、test/browser coverage、false pass、critical issue miss。
- actual token usage、context bytes、LLM call count、python-only ratio、cache hit ratio、expansion count、latency。

只有在质量边界不退化、无 P0/P1 关键遗漏、E1 和批准链保持有效，并且效率指标获得可复现实质改善时，才能考虑后续正式推广。

## 5. F14-B 最小实施清单

本轮只为 F14-B 规划以下内容：

| 项目 | Python 责任 | 不做什么 |
| --- | --- | --- |
| Artifact Index | 定位、hash、authority、revision、freshness、locator | 不覆盖 project.yaml，不推断业务状态 |
| Dependency Graph | 记录明确的 requirement/AC/issue/file/test/evidence/reference 边 | 不根据关键词猜测硬依赖 |
| Diff Index | approved baseline 到 current revision 的确定性差异 | 不把未知变更标记为无影响 |
| Test Parser | 结构化解析和短 summary | 不删除原始日志，不让模型解析本可由 Python 解析的结果 |
| Source Hash Cache | 减少重复读/解码/hash/解析 | 不跳过授权、freshness、Secret 和 policy |
| 负向测试 | stale、mutation、unknown、越权、缺失 locator、循环/超预算 | 不通过删除历史或降低门槛来“修复”测试 |
| 报告/Telemetry 草案 | 记录可重建输入和输出 | 不把估算 token 冒充真实 token |

F14-B 明确不做：

- 不减少现有正式 Role Run 的 LLM 调用。
- 不实现跨角色 Context 或结果复用。
- 不用摘要替代用户需求、批准计划、产品规格或保护条件。
- 不修改 F10-F13 的状态、CAS、Lease、恢复和 append-only 规则。
- 不改变 Evaluator 独立性、Mandatory Regression、Browser Gate 或安全策略。
- 不在 Skill 仓库创建 project.yaml，不把测试项目数据写入 Skill 仓库。

## 6. 必须保留的系统不变量

1. project.yaml 是项目状态唯一来源；Index、Graph、Cache、Summary 都可删除后重建，但本轮不执行删除。
2. 所有历史工件、反馈、选择、批准、计划、评估和证据均追加式保存，不覆盖旧记录。
3. User Authority > Approved Source > System Inference；模型推断不能覆盖用户明确事实和已批准来源。
4. Mandatory Context 不完整时，必须扩展、升级验证或阻塞，不能继续调用并假设完整。
5. Context budget 是效率约束，不是质量门；质量需要更多上下文时可以受控扩展。
6. E1 Evaluator 永远保持 fresh、独立、当前 revision、独立证据。
7. Runtime 验证状态、路径、权限、hash、revision、来源和证据；模型不拥有这些写权限。
8. unknown、stale、缺失和冲突必须显式记录。
9. 所有缓存都必须受 role、revision、policy、source hash 和 parser/model version 约束。
10. 每个阶段必须有可重现测试；没有证据不能宣称 PASS。

## 7. 故障注入与回归要求

F14-B 至少需要补齐以下测试场景，后续阶段继续复用：

1. Mandatory source 被删除或变更。
2. approved plan hash 与 index 不一致。
3. project revision 在 build 与 commit 之间变化。
4. policy hash 或 parser version 变化导致缓存失效。
5. unknown dependency、循环扩展、扩展次数超限。
6. 跨 role 或越权路径请求 Context。
7. Secret、非法 locator、stale evidence、损坏 summary。
8. 原始测试输出很长、退出码非零、JUnit/pytest 结果不完整。
9. Evaluator 试图读取 Generator 非证据历史或复用其 cache。
10. Crash recovery、retry、rollover、CAS 冲突后不能重复生成业务工件、事件或批准记录。

当前环境的临时目录不可写，必须在执行完整回归前解决测试运行环境，否则只能标记为“测试未完成”，不能用历史报告代替当前证据。

## 8. 交付与审批顺序

1. F14-A：两份审计/计划文档，等待用户确认。
2. F14-B：确定性索引、图、差异、解析、hash cache 和测试；等待 Runtime 评审。
3. F14-C：Mandatory/Task Relevant/On Demand 与 Coverage Gate；等待产品/质量边界评审。
4. F14-D：结构化 Context Escalation 和 L1/L2/L3；等待安全与 E1 回归。
5. F14-E：增量 Context 与可验证摘要；等待失效/恢复回归。
6. F14-F：Invocation Gate；必须先有前述覆盖、授权、telemetry 和 benchmark 证据。
7. F14-G：统一效率 Telemetry。
8. F14-H：test_ 项目 A/B Benchmark、TEST_REPORT.md、归档与最终决策。

任一阶段若发现批准链、E1、CAS/Lease、Mandatory Regression、证据可追溯性或安全边界退化，应停止自动推进，追加问题/变更记录，不能用扩大 token 或降低验收阈值掩盖问题。

## 9. 当前计划状态

本计划尚未获得产品方案确认、正式 Plan 批准或实现授权。根据仓库规则，F14-A 完成后应等待用户确认；在用户未明确批准前，不生成 Generator 可执行的 approved_plan，也不修改 code/ 或 Runtime 实现。

## 10. F14-A Plan Hardening 修订（追加式）

本节是对原计划的追加修订，不覆盖前面已经形成的历史路线。它把 F14-B 前必须定稿的底层语义、测试基线、Telemetry 顺序和 Go/No-Go 条件固化下来。

### 10.1 当前基线与环境结论

测试专用目录位于 Skill 仓库之外：

    C:\Users\28388\Desktop\f14-a-temp-tests-20260811

测试进程显式设置 TEMP、TMP、TMPDIR 后，Python tempfile 创建/清理验证通过。

当前回归结果：

| 集合 | 结果 | 证据状态 |
| --- | --- | --- |
| F14-A 重点集合 | 120/120，0 error，0 failure，1.332 秒 | Current Run Verified |
| tests 完整集合 | 421/421，0 error，0 failure，26.975 秒 | Current Run Verified |
| 前一轮默认临时目录运行 | 29 个环境错误 | Environment Failure，不能作为当前产品失败 |
| F13 历史报告 | 历史测试数字 | Historical Test-Proven，不替代当前结果 |

因此，F14-B 的测试基线前置条件已经满足。后续每一阶段仍必须在可写临时目录中运行当前测试，不能用历史报告替代。

### 10.2 三类效率指标域

F14 的指标不再把文件读取、Context 交付和模型调用混成一个“浪费”：

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

Runtime Efficiency 改善不能直接宣称 Token Efficiency 改善。若 Host 无真实 token usage，token 字段必须为 estimated 或 unavailable；bytes 不得冒充 tokens。

### 10.3 Source Cache 最终设计

F14-B 的 Source Cache 采用两层模型。

#### Layer 1 — Source Fingerprint / Locator Cache

字段包括 canonical locator、file identity、size、mtime/filesystem metadata、project revision、policy hash、role scope、parser version 和 previously known content hash。

用途是快速定位、减少重复目录扫描并判断是否需要重新读取。Layer 1 不是内容 authority，mtime + size 不能代替 content hash。

#### Layer 2 — Content Addressed Cache

结构为：

    content_hash
      -> decoded content
      -> parsed representation
      -> deterministic summary/index result

decoded content 可以按可信 content hash 复用；parsed representation 和 summary 必须再受 parser/version、policy、role scope 和 source kind 约束。受 E1 限制的语义结果不跨角色复用。

#### Invalidation Rules

1. locator、file identity、路径授权或 workspace 边界变化：Layer 1 失效。
2. size、mtime 或其他 metadata 变化：标记可疑并触发重新读取；不能直接当作内容证明。
3. protected/security-sensitive/用户明确约束/approved source/revision/policy 发生变化：强制重新读取并重新 hash。
4. revision、policy、role scope、parser/version 不匹配：对应解析和 summary 失效。
5. 新 content hash 与 previously known hash 不一致：旧派生结果失效，重新解析。
6. cache schema、checksum、content hash、parsed output hash 或 locator 校验失败：标记 corruption，fallback 到重新读取。
7. fast fingerprint 只能服务于非权威、非安全敏感的优化提示；Mandatory、Security、E1 和批准来源必须走可信内容校验。
8. 文件不可读、权限改变、符号链接异常、locator 不可解析：返回 stale/unknown，不假设未变化。
9. cache hit 之前仍需执行 path policy、Secret scan、revision freshness 和 role authorization。
10. hit/miss、invalidation reason、fallback read 和 parser version 都必须进入 Minimal Telemetry。

### 10.4 Dependency Graph 最终 Edge Model

不构建语义模糊的单图边。逻辑上分成：

1. Authority / Provenance Graph：derived_from、approved_by、supersedes、sourced_from、governed_by。
2. Execution / Verification Dependency Graph：satisfies、implements、affects、verifies、evidenced_by、regresses_with。

如果物理上使用统一 Graph 数据结构，每条 Edge 必须有：

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

只有明确 ID、approved source、exact locator、当前 revision 和可验证证据支持的关系才可为 confidence: explicit。无法证明的关系只能为 unknown 或不进入硬依赖集合。禁止基于文件名、关键词、模型猜测或模块名相似建立硬依赖。

### 10.5 Complete Mandatory Requirement Coverage

Coverage Gate 的正式目标为 Complete Mandatory Requirement Coverage，而不是 Always Load Entire Requirements Document。

对 task、role、revision，Runtime 必须证明以下集合已覆盖：

- 直接相关 Requirements。
- 相关 Acceptance Criteria。
- 用户明确硬性要求。
- 用户明确禁止事项。
- Global、Security、Privacy、Data、Compatibility、Regulatory Constraints。
- Approved Scope。
- 当前 Change Request 范围（如适用）。
- 当前 revision 和 authoritative source binding。

每个 Mandatory 条目必须绑定 structured index + exact authoritative source unit，并记录 id、authority、source_ref、source_hash、exact_source_locator、revision 和状态。AI Summary 只能导航，不能替代权威单位。

Coverage 状态包括 COVERED、MISSING、STALE、CONFLICT、UNKNOWN、NOT_APPLICABLE。只有所有适用条目为 COVERED，或有权威范围证据证明 NOT_APPLICABLE，Coverage Gate 才能通过。UNKNOWN 不得默认视作无影响。

因此 Generator/Evaluator 不需要每次把 active_requirements 全文放入 Context，但必须获得当前任务所需的精确 source units，并由 Runtime 证明 Complete Mandatory Requirement Coverage。

### 10.6 Artifact Index 存储位置

Artifact Index、Dependency Graph、Source Cache 和 Diff Index 均放入现有 F10 Control Plane / Runtime Store 的派生数据区。对于 schema v7 managed project，最终使用项目 .runtime/sessions.sqlite3 的 derived tables/受控派生结构；由外部 F10 Control Plane 托管时，使用该 Control Plane 的 sessions.sqlite3。Skill 本体仓库不创建 project.yaml 或运行时 Store。

这些结构可删除后重建，不是 project.yaml 的替代状态源，不写回项目业务目录，不改变业务状态。损坏时只能触发重建、stale/unknown 或阻塞。

### 10.7 Test Output Parser 语义

输入优先级固定为：

1. Existing Structured Runtime Result。
2. JUnit/machine-readable test result。
3. Structured pytest-compatible result。
4. Human console parsing fallback。

Parser 无法可靠判断时返回 parse_status: UNKNOWN，不能自动生成 passed: true。必须保存 parser version、source locator、raw log locator、command、exit code、parse confidence/status，原始 evidence 保留。

### 10.8 Change Request 与 Invocation 统计

Change Request 的确定性 Runtime lifecycle record 不是主要 Token 浪费，不为减少 record count 删除它。Telemetry 增加 execution_type：

~~~yaml
execution_type:
  python_only
  llm
  perception
  reused_deterministic
  rollover
~~~

主要效率指标是 Real LLM Invocation Count，而不是 invocation record count。

### 10.9 F14-F 结果复用边界

F14-F 第一版只允许 Deterministic Python Result Reuse：

- file hash。
- Artifact Index。
- Test Parser Result。
- Diff。
- Browser deterministic evidence summary。
- protected snapshot verification。

F14-F 第一版禁止 LLM Semantic Result Reuse，包括 Evaluator PASS/FAIL、Generator implementation output、Planner product decision 和 Generator Context 复用给 Evaluator。Evaluator 每轮仍需 fresh invocation、current revision、independent context、independent evidence interpretation。

### 10.10 Telemetry 前移后的新顺序

最终顺序调整为：

    F14-B Deterministic Foundation + Minimal Telemetry
      ->
    F14-C Context Architecture
      ->
    F14-D Context Escalation
      ->
    F14-E Incremental Context
      ->
    F14-G Full Telemetry + Baseline Benchmark
      ->
    F14-F Invocation Gate
      ->
    Final A/B Benchmark

F14-B 至少开始记录 file read、hash operation、parser execution、cache hit/miss、context source count、context bytes 和真实 LLM invocation count（若已有可观察入口）。不先优化再补仪表。

### 10.11 Project Effort Profile 后置

Project Effort Profile 继续保留为后置设计，F14-B 不实现，也不参与 Context 裁剪。只有 Mandatory Coverage、Context Escalation、Incremental Context、Telemetry 和 Quality Regression 稳定并有证据后，才进入单独阶段设计。接入时必须证明 product_complexity 不会压低 design_depth、evaluation_depth 或安全验证深度。

### 10.12 A/B Benchmark 场景补充

F14-H/Final A/B 至少包含：

- Case A：简单产品 + 简单设计，例如普通倒计时工具。
- Case B：简单产品 + Showcase Design，例如核心业务简单但要求 premium/showcase 视觉、高质量动画、精细交互和高设计完成度的倒计时 App。
- Case C：标准项目，例如个人记账 App。
- Case D：高复杂度项目，例如包含多角色、数据导入、报表、外部集成和权限的企业销售分析系统。

Case B 必须验证 product_complexity: low 不会错误导致 design_depth: low 或 evaluation_depth: low。

### 10.13 F14-B Go/No-Go Gate

只有以下条件全部满足才允许进入生产实现：

1. Windows 临时目录问题已确认并通过显式临时目录复验。
2. F14 重点回归可当前复现。
3. 当前完整 baseline 已记录。
4. Source Cache 两层模型和 invalidation rules 已定稿。
5. Dependency Edge 语义已定稿。
6. Complete Mandatory Requirement Coverage 已定稿。
7. Artifact Index 的 F10 Runtime Store 存储位置已定稿。
8. Test Parser 的输入优先级和 UNKNOWN/fallback 已定稿。
9. F14-F 第一版 semantic result reuse 禁止规则已定稿。
10. Minimal Telemetry 已前移进入 F14-B。

上述十项已在本 Hardening 追加中满足，因此 Go/No-Go 结论为：

**GO（仅允许下一次用户明确授权后进入 F14-B；本轮不启动生产实现）。**

## 11. F14-B 最小实施范围与顺序（仅供下一次授权使用）

如果用户下一次明确授权，F14-B 只按以下顺序实施：

1. 先加入 Minimal Telemetry 数据模型和不改变行为的观测入口。
2. 实现可重建的 F10 Runtime Store 派生 Artifact Index 存储边界。
3. 实现分离 graph_kind 的 Authority/Provenance 与 Execution/Verification Edge Model。
4. 实现 Diff Index 的确定性输入和 stale/unknown 语义。
5. 实现两层 Source Fingerprint/Content Addressed Cache，先覆盖纯确定性读取/解析。
6. 实现 Test Output Parser 的结构化优先、UNKNOWN/fallback 和原始 evidence locator。
7. 增加 mutation、stale、corruption、越权、unknown、revision/policy/parser invalidation 和 crash/recovery 测试。
8. 在重点集合和完整集合中重跑当前 baseline，确认正式 Role Run 的模型调用语义未变化。

本清单不是实现授权。F14-B 生产代码必须等待用户下一次明确授权。
