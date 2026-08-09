# Harness Adaptive Enforcement Implementation Report V001

## 实际修改文件

### Browser 与 Candidate

- `runtime/browser/models.py`、`runtime/browser/evidence.py`、`runtime/browser/__init__.py`
- `config/browser_scenarios/web_app.yaml`
- `config/evaluation_rules/web_app.yaml`
- `config/schemas/browser_scenario_manifest_v1.schema.json`
- `scripts/best_candidate.py`、`runtime/candidates.py`
- `config/candidate_policy.yaml`
- `config/schemas/candidate_v2.schema.json`

### Runtime 强制执行

- `runtime/phase_runner.py`
- `runtime/contract_preflight.py`
- `runtime/harness_policy.py`
- `runtime/context/rollover.py`、`runtime/context/__init__.py`
- `runtime/event_types.py`、`runtime/session_store.py`

### Calibration、Benchmark、接线

- `scripts/evaluator_calibration.py`
- `scripts/benchmark_runner.py`
- `config/harness_policy.yaml`
- `config/schemas/calibration_observation_v1.schema.json`
- `config/schemas/benchmark_result_v1.schema.json`
- `config/schemas/harness_policy_v1.schema.json`
- `config/workflow.yaml`、`config/context.yaml`
- `scripts/approval.py`、`scripts/implementation_contract.py`（包导入兼容）
- `prompts/generator_prompt.md`、`prompts/evaluator_prompt.md`

### 新增测试

- `tests/test_phase_runner.py`
- `tests/test_candidate_profile_binding.py`
- `tests/test_harness_adaptive.py`
- `tests/test_benchmark_runner.py`

## 问题与解决

- Browser 空场景和纯观察动作绕过：Manifest + required scenario + 真实业务动作 + AC 映射 Gate。
- Web Candidate 跳过 Browser：Candidate v2 写入、选择和恢复均检查 Profile/Required Gates。
- Prompt 依赖的模型调用：PhaseRunner 通过 Adapter、Context、Invocation、verifier 和 CAS 闭环执行。
- 高风险实现绕过 Contract：Generator Preflight 调用已存在的来源链和 Contract history 校验。
- 固定 Harness 深度：Harness Policy 对未知模型/风险/证据不足 fail-closed 到 FULL。
- Calibration 误称 QA：新增 blind observation 校验和独立人工 adjudication 指标。
- 缺少受控 Benchmark：新增仓库外 `test_` 项目隔离的追加式 Runner。

## 兼容与限制

- 旧 Candidate v1 只读保留，未静默升级历史含义；新产物使用 v2。
- PhaseRunner 的 Evaluator 成功路径必须由 Host 注入真实 Gate verifier；没有 verifier 会拒绝执行，不会靠模型声明放行。
- Benchmark Runner 只记录受控实验指标，不自动改变正式项目批准、状态或安全规则。
- 项目 YAML 受限序列化器不支持浮点，Benchmark 报告落盘时将浮点稳定化为字符串，内存 API 仍保留数值。
- Docker 依赖本机 daemon，本轮环境不可用，未用本地替代品伪造结果。
