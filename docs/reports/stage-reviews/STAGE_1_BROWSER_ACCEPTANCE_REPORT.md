# Stage 1 — Evaluator Browser Acceptance Harness

日期：2026-08-09

## 结论

`STAGE RESULT: PASS`

## Implemented

- 新增 `runtime/browser/`：BrowserAdapter、PlaywrightBrowserAdapter、BrowserPolicy、
  BrowserBroker、BrowserHarness、结构化 Browser Run/Step Evidence。
- BrowserBroker 复用 Session、Role Run、Worker Lease 和 F12 `browser.access`；
  Browser Policy 默认拒绝、只允许 Evaluator、要求 Profile 启用并限制 base URL 同源。
- 支持第一版确定性动作：启动、导航、点击、填写、选择、键盘、等待、可见文本、
  DOM/URL 检查、截图、console error / failed request 捕获、关闭。
- `web_app` Profile 增加 `browser_validation` 和必需的
  `GATE-BROWSER-ACCEPTANCE`；default Profile 不强制 Browser。
- 现有 Evidence Manifest 增加 `browser_runs` / `browser_evidence` 校验和引用，
  Gate 能区分 `implementation_failure` 与 `evaluation_environment_blocked`。
- Evaluator Prompt 只声明如何读取 Profile 与记录证据，不把业务验收规则硬编码进 Prompt。

## Tests

- Stage 1 定向：`59 passed, 7 subtests passed`
- 相关 F9–F13 / Execution / Security / Context / Snapshot / E2E 回归：`98 passed`
- `python -B` 正式 Browser Policy 导入 smoke：PASS
- `git diff --check`：PASS

## Regression

- 既有 F9 Evidence Manifest、Gate 顺序、F10 CAS / Lease、F11 ExecutionBroker、
  F12 Security、F13 Context、Snapshot 和 Change Request E2E 均通过。
- Browser 不成为第四 Agent，也不直接写 `project.yaml`。

## Known limitations

- 当前执行环境没有安装 Python Playwright，也没有真实 Web App 可供本轮启动；
  已覆盖其明确的 `evaluation_environment_blocked` 分类路径。
- Profile 的 `startup_command` 已作为配置输入保留；启动应用进程的执行仍应由受控
  ExecutionBroker / Evaluator 执行，不由 Browser 直接绕过 F11。
- Docker Sandbox 仍为 `DEFERRED`。

## Architecture impact

- 新增能力位于 Runtime Infrastructure / Broker / Policy / Harness 层，未增加 Agent。
- Browser Evidence 通过现有 Manifest 和 Gate 进入 F9 闭环，旧的非 Web manifest 不变。

## Next stage

- Stage 2：增加 Feature Completeness / Anti-Stub 的 Profile、确定性静态检查和 Gate，
  并与 Browser / Requirement Evidence 组合验证。
