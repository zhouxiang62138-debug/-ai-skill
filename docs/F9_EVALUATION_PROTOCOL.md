# F9 结构化验收与返工协议

## 工件关系

每轮验收保留三类来源：

1. `evaluation/reports/evaluation-<nnn>.md`：供人阅读；
2. `evaluation/issues/evaluation-<nnn>.yaml`：Evaluator 的机器可读事实；
3. `memory/handoffs/responses/evaluation-<nnn>-response.yaml`：Generator
   对来源 Issue 的逐项回应。

三者均为追加式历史工件。文件名、`evaluation_id`、`issue_id` 和引用关系由
`scripts/evaluation_protocol.py` 确定性校验。

## Issue 分类与路由

- 实现、测试、缺测试、不完整实现和回归路由到 Generator；
- 计划缺口和范围不一致路由到 Planner；
- 需求歧义路由到用户；
- 环境阻塞进入 `BLOCKED`；
- 未授权修改作为 `blocker` 路由到 Generator 修复；
- 证据缺失必须说明来源，再分别路由到 Generator、Planner 或 `BLOCKED`。

多类问题并存时使用：

`SYSTEM_OR_USER > USER > PLANNER > GENERATOR > ACCEPTED`。

## 严重度与 PASS

严重度为 `blocker`、`critical`、`major`、`minor` 和 `observation`。
开放的 blocker 或 critical、未执行的必需验收、缺少必要 handoff、证据不完整、
受保护工件未授权修改，或上轮 blocking Issue 未回应，均禁止 PASS。评分仅是附加
信息，不能覆盖这些硬条件。`observation` 本身不阻止验收。

## 需求追踪

功能性 Issue 优先复用正式计划中的 Requirement ID 与 Acceptance Criterion ID。
旧计划没有稳定 ID 时，使用 `UNMAPPED` 或 `PARTIALLY_MAPPED` 并写明原因，禁止
临时伪造 ID。旧项目没有 Issue Package 时仍可读取原 Markdown 报告，但下一轮
新 Evaluation 使用当前协议。

## Generator Response

Response 支持 `FIXED`、`PARTIALLY_FIXED`、`NOT_FIXED`、
`CANNOT_REPRODUCE`、`NEEDS_CLARIFICATION` 和 `OUT_OF_SCOPE`。
每种状态都有不同的必填事实。所有 blocking 和 critical Issue 必须逐项回应；
未知 Issue ID、来源不匹配和命令结果不一一对应均会拒绝交接。

## 生命周期与复验

Issue 生命周期为 `OPEN`、`ACKNOWLEDGED`、`RESOLVED`、`REOPENED`、
`DEFERRED` 和 `INVALID`。同一根因沿用稳定 ID。Generator 的修复声明只决定复验
优先级，Evaluator 的实际结果才决定 `RESOLVED` 或继续 `OPEN`；已解决问题再次
失败标记为 `REOPENED`。

## 事务与恢复

提交顺序固定为：校验全部输出、暂存 Issue、暂存 Markdown、提交两个追加式工件、
最后原子更新 `project.yaml`。任一暂存失败都不更新状态；状态写入失败时保留完整
工件和 `RECOVERY_REQUIRED` 日志，供恢复流程继续，禁止覆盖历史 Evaluation。

## 常见错误

- 使用绝对路径、UNC 或 `..`：拒绝；
- Issue 文件名与 Evaluation ID 不同：拒绝；
- `evidence_missing` 未说明缺失来源：拒绝；
- `FIXED` 没有修改文件或验证结果：拒绝；
- 高分但存在 blocker/critical：FAIL；
- 用旧 Issue ID 回应另一轮 Evaluation：拒绝。

## 测试

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m unittest -v tests.test_evaluation_protocol
```

## Evidence Manifest 与 Gate

每轮证据保存在 `evaluation/evidence/evaluation-<nnn>/`。Manifest 记录非敏感
环境信息、参数数组命令、起止时间、退出码、stdout/stderr 路径、检查结果、Gate、
Requirement/Acceptance Criterion 和 Issue 的交叉引用。

Gate 固定顺序为交付完整性、构建与静态检查、自动化测试、需求验收、回归检查、
非功能要求和证据完整性。是否必需由当前 evaluation profile 决定；必需 Gate
未执行、无证据或被跳过均不能 PASS。

命令执行使用 `shell=false`、Profile 白名单、项目内 cwd、超时、输出上限和敏感
值脱敏。命令不存在记录为环境 `BLOCKED`，非零退出码记录为失败，不能伪造成
成功。

受保护工件由 SHA-256 快照检测，不依赖 Git。修改、缺失或新增均会生成
`unauthorized_change` blocker，包括正式计划、规格、批准记录、Profile、Schema
与测试。

## 增量复验与循环治理

返工后的验收包含针对性复验和最小强制回归。针对性复验覆盖上轮 OPEN/REOPENED、
Generator 声称 FIXED/PARTIALLY_FIXED/CANNOT_REPRODUCE，以及受改动文件影响的
验收标准和测试；mandatory regression suite 每轮执行，不能跳过。

Issue 通过 Requirement、Acceptance Criterion 和根因签名保持跨轮稳定 ID。
每轮记录 resolved、reopened、repeated、new、regression、blocker/critical 变化，
并确定性计算 `IMPROVING`、`STABLE`、`REGRESSING`、`STALLED` 或 `UNKNOWN`。

重复问题、重复失败修复声明、连续回归、路由争议或连续无进展达到阈值时提前升级，
停止自动 Generator 循环。只有路由到 Generator 的可返工 FAIL 增加轮次；BLOCKED、
WAITING_FOR_USER 和 Planner 路由不增加。第五次失败进入 `WAITING_FOR_USER` 并
生成追加式决策摘要。新 Plan 与新批准记录同时有效时可开启新序列，但历史不清除。
