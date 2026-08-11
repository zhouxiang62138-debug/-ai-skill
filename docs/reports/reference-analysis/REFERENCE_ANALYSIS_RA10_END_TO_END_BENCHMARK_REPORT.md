# RA10 Real End-to-End Reference Benchmark 阶段报告

## 阶段

RA10 — Real End-to-End Reference Benchmark

## 状态

PASS — READY_WITH_LIMITATIONS

本阶段使用 pytest 隔离临时根创建 `test_reference_e2e`，不读取或修改真实用户项目。测试项目按规则在 `archive/TEST_REPORT.md` 保存结果；pytest 完成后由临时运行环境清理，不向 Skill 源码目录写入项目工件。

## 场景结果

- No Reference：项目代码保持不变，Reference 目录不会被无请求污染。
- Text Reference：受控 UTF-8 文本 artifact 读取、大小/哈希校验和文本 Normalize 通过。
- Project-local Image：Local Image Acquisition、Manifest REQUESTED→STARTED→SUCCEEDED、SHA-256 通过。
- User-attached/Native Perception：当前明确 `UNAVAILABLE`，无伪造 Finding。
- Public Web URL：当前明确 `BLOCKED_BY_ENVIRONMENT`，无未经授权网络抓取。
- Multi-reference：Fusion 保留 deterministic 观察；视觉能力不可用时结果为 `BLOCKED` 并保留 limitation。
- Malicious input：由 RA9 对抗套件覆盖并通过。

## 验证证据

- RA10 隔离 E2E fixture：`1 passed`
- Fixture archive report：`test_reference_e2e/archive/TEST_REPORT.md`
- 最终全量回归：`723 passed, 5 skipped, 113 subtests passed`
- Docker skip：5 个，全部为环境不可用，已在最终报告中明确说明

## 已知限制

1. Host 没有注入真实 Codex Native Multimodal bridge，User-attached Image 场景只验证真实 limitation。
2. Host 没有注入真实 Browser Capture Adapter，Public Web 场景只验证受控阻断。
3. Generator/Evaluator/Design Exploration 的完整 CR 真实项目链路继续由既有 R0-R6 测试和 RA8 binding 契约覆盖，尚未声称新的生产项目已自动发布。

## Gate 决策

CONTINUE — 隔离 E2E、真实 limitation 标记和 archive 报告已具备；进入 FINAL Production Closure 的最终自审计。
