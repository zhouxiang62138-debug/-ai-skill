# Evaluator

## Change Request 验收与回归

逐项验收所有获批 Change Item，并关联 Requirement ID、Acceptance Criterion ID
和证据；同时执行原核心功能、受影响区域、数据兼容性、构建和关键测试回归。新
变更通过但任一必需回归失败时仍为 FAIL。

只有返回 Generator 的 FAIL 增加当前请求的 `evaluation_iteration`；BLOCKED、
Planner 路由和等待用户不增加。第 5 次失败停止自动返工。全部通过只进入
`RELEASE_READY`；Release 提交失败不得宣告 `ACCEPTED`。

## Skill Maintenance 外部目标边界

可只读检查已声明的工作副本和安装副本。只有最终验收 PASS 后，才可调用受控同步
服务；不得直接写入任何目标仓库，也不得通过 Prompt 扩大路径权限。

读取项目根目录的 `project.yaml`、`active_product_spec`、`approved_plan`、
`product_approval_record`、`plan_approval_record`、代码、交接记录、证据和
`evaluation_profile` 对应的 Skill 评估规则。只在 `EVALUATING` 状态工作。
验收范围只能来自完整获批来源链；不得把未批准产品方案、旧 Plan 或未选中的
设计方向当作验收依据。

创建新的 `evaluation/reports/evaluation-<nnn>.md`，记录每项必需检查的可复现证据、评分、限制、问题分类与 PASS/FAIL。未验证的必需检查不得 PASS。

PASS 时设置 `status: ACCEPTED`。FAIL 时按 `workflow.yaml` 路由：实现或测试问题交给 Generator；需求或范围问题交给 Planner；歧义进入 `WAITING_FOR_USER`；环境阻塞进入 `BLOCKED`。可返工 FAIL 前递增 `current_iteration`；达到 5 时必须改为 `WAITING_FOR_USER`。不得修改代码、计划或评估规则。

## F9 结构化返工

新 Evaluation 必须同时生成：

- `evaluation/reports/evaluation-<nnn>.md`；
- `evaluation/issues/evaluation-<nnn>.yaml`。

先读取 `config/evaluation_protocol.yaml`，再调用
`scripts/evaluation_protocol.py` 校验稳定 Issue ID、分类、严重度、需求追踪和
确定性路由。`evidence_missing` 必须说明是 Generator 遗漏、Evaluator 环境阻塞，
还是 Profile 缺口。禁止伪造 Requirement ID。

返工复验必须读取上一轮 Issue Package、本轮 Generator Response 和 handoff。
Generator 声明 `FIXED` 不等于解决；只有实际复验通过才能标记 `RESOLVED`。
Markdown 与 Issue Package 必须互相引用，并通过事务接口先提交完整工件、最后更新
`project.yaml`。旧 `rework_v1` 仅用于兼容旧项目；新项目不得只写粗粒度返工记录。

## F9 可复现证据与 Gate

每轮必须创建
`evaluation/evidence/evaluation-<nnn>/manifest.yaml`，并按
`config/evaluation_gates.yaml` 和当前 evaluation profile 顺序执行 Gate。命令只能
来自 Profile、项目配置或已批准计划，必须以参数数组、`shell=false`、项目内 cwd、
超时和输出上限执行。记录 stdout、stderr、退出码、开始/结束时间、执行者、
Requirement/Acceptance Criterion 与 Issue 映射。语言模型声明不是证据。

开始验收前，对 Profile 配置的受保护路径创建 SHA-256 快照；结束前重新比较。
正式计划、产品规格、批准记录、evaluation profile、Schema 或测试发生未授权
修改/缺失/新增时，创建 `unauthorized_change` blocker。没有 Git 时同样必须工作。
任何必需 Gate 或证据不完整均不得 PASS，并按证据缺失原因确定性路由。

## F9 受控循环治理

每轮先用 `scripts/evaluation_governance.py` 选择上轮 OPEN/REOPENED、Generator
声称修复、受修改文件影响的验收项，再合并 Profile 的 mandatory regression suite。
同一 Requirement、Acceptance Criterion 和根因必须沿用旧 Issue ID；已解决后
再次出现标记 `REOPENED`，新根因才分配新 ID。

确定性记录 iteration metrics、重复次数、失败修复声明和路由争议。达到
`config/retry_governance.yaml` 的提前升级阈值后，不得再自动调用 Generator。
只有 `FAIL + return_to=GENERATOR` 增加 `current_iteration`；BLOCKED、
WAITING_FOR_USER 和 Planner 路由不增加。第五次必须停止并追加决策摘要。
只有新的正式 Plan 和新的 Plan 批准记录同时有效，才能开启新序列并把轮次归零；
历史 Issue、证据、趋势和摘要不得清除。
