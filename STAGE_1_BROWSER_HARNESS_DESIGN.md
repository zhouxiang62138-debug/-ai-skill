# Stage 1 — Evaluator Browser Acceptance Harness 设计

日期：2026-08-09

## 目标

让 Evaluator 在 `web_app` Profile 下通过确定性的 Browser Harness 操作运行中的
Web App，并将每一步与 Evaluation、Requirement、Acceptance Criterion 和现有
Evidence Manifest 关联。

## 架构

```text
Evaluator
  ↓
BrowserBroker
  ↓ Role → CapabilityPolicy → BrowserPolicy
  ↓
BrowserHarness
  ↓
PlaywrightBrowserAdapter（可选运行时依赖）
  ↓
running application
```

Browser 不是 Agent。BrowserBroker 复用现有 Session、Role Run、Worker Lease 和
`browser.access` Capability；BrowserPolicy 还会检查 Profile 是否声明
`browser_validation.required: true`、动作白名单和 base URL 同源边界。

## 第一版能力

- start / close
- navigate / click / fill / select / keyboard
- wait for selector/state
- read visible text
- inspect DOM state / URL
- screenshot
- 捕获 console error 与 failed request

操作采用结构化参数，禁止复杂视觉 AI、任意脚本注入和任意 URL 导航。

## Playwright 依赖策略

Playwright 通过延迟导入接入，仓库本身不在没有依赖时伪造成功。未安装或无法启动
浏览器时返回 `environment_blocked`，由 Browser Gate 生成 BLOCKED；选择器、状态或
验收行为失败则返回 `implementation_failure`，生成 FAIL。

## Evidence 集成

现有 `manifest.yaml` 增加可选的：

- `browser_runs`：Browser Run 摘要；
- `browser_evidence`：每个 Browser Step 的 action、target、expected、observed、
  result、screenshot reference、console errors、network failures 和 timestamps。

每个步骤包含 `evaluation_id`、`requirement_id`、`acceptance_criterion_id`、
`browser_run_id` 和 `step_id`。大型截图只写项目内相对路径，manifest 保存引用。

## Gate / Profile

- `GATE-BROWSER-ACCEPTANCE` 仅当 Profile 声明 `browser_validation.required: true`
  时配置为硬 Gate。
- `web_app` Profile 要求该 Gate，并声明 startup command、base URL、timeout、截图、
  console 和 network failure 策略。
- `default` Profile 不启用 Browser，非 Web 项目不被强制要求浏览器。

## 安全与回归

- 不创建 Browser Agent，不修改 project.yaml 直接状态，不绕过 F12。
- Browser evidence 通过现有 manifest 校验和 `evidence_manifest` 引用进入业务闭环。
- 保持既有 Gate 顺序；没有 Browser 配置的旧 manifest 行为不变。
- Playwright 未安装时仅影响真实 Browser 运行，不影响纯 Python Runtime 回归。
