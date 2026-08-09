# Harness Adaptive Enforcement Final Evaluation V001

## 结论

本轮 Harness 强制执行与自适应优化已在 Skill 本体仓库完成代码、Schema、配置、Prompt 和最小 Runtime E2E 覆盖。现有三 Agent 边界、F9–F13、CAS、Lease、Recovery、Capability、Secret Boundary 和历史追加式约束保持不变。

## 验收判断

- 既有与新增自动化测试：通过，最终 `592 passed`。
- 允许的 5 个 skip：仅 Docker daemon unavailable，已明确标记，不影响非 Docker 测试，但 Docker 相关能力不能宣告 PASS。
- Browser 强制：对必需 Profile 结构化场景、业务动作、AC traceability、环境 BLOCKED 已由 Runtime Gate 约束。
- Candidate 强制：v2 已绑定 Profile/Hash/Rubric/Model/Calibration/Required Gates；v1 只读兼容。
- PhaseRunner：已实现 Adapter、Context、Invocation、Preflight、Gate verifier、失败持久化、幂等和 CAS 顺序。
- Contract：高风险输入在 Generator 前置阶段可确定性拒绝缺失/无效 Contract。
- Adaptive Harness：未知模型、未知风险、证据不足 fail-closed 到 FULL；LEAN 不跳过 mandatory gates。
- Calibration：已有 12 case 保留为确定性路径校准；blind 输入不携带最终判断，人工复核指标独立保存。
- Benchmark：已提供隔离 Runner，但本轮没有执行真实外部 Benchmark 数据。

## 未完成项与 Known Limitations

1. PhaseRunner 需要宿主注入真实 Evaluator Gate verifier；仓库只提供强制接口和拒绝路径，没有接入某一厂商模型 SDK。
2. Browser 场景清单示例使用抽象 Profile 场景，真实项目仍必须提供自己的 Requirement/AC 和 API/持久化证据。
3. 本轮没有启动真实 web app，也没有 Playwright 真实业务 E2E；因此真实业务 Browser Acceptance 只能在外部 managed test project 中继续验证。
4. 本轮没有创建或归档 managed test project，故没有在 Skill 目录内写入测试项目数据，也没有伪造 `TEST_REPORT.md`。
5. Docker daemon 不可用；Docker 执行环境保持 DEFERRED/BLOCKED。

## 是否适合可信任本机项目

适合继续作为本机项目的 Runtime 强制层：来源链、步骤、Gate verifier、CAS、Lease、幂等和安全边界有可复现证据。是否适合生产或不可逆代码，仍需在仓库外受控 `test_` 项目完成真实 Browser、Persistence、Integration、Regression、Docker 和人工 Calibration/Benchmark；本轮不替这些证据背书。

## 下一步优化是否有 Benchmark 支撑

当前没有真实 Benchmark 数据支撑任何“更便宜、更快或质量更高”的模型/Harness 结论。下一步应先在仓库外执行五种受控变体，记录真实模型、版本、Token、成本、浏览器覆盖、关键 Bug 漏检和人工复核分数，再依据数据调整 Policy；在此之前未知输入继续默认 FULL。
