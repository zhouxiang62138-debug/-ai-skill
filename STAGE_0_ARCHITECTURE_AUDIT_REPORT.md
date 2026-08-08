# Stage 0 — Current Architecture Audit

日期：2026-08-09
范围：Skill 本体仓库，只读审计与文档状态修正

## 结论

`STAGE RESULT: PASS`

## Implemented

- 新增 `docs/RUNTIME_CAPABILITY_STATUS.md`，作为 Formal Production Runtime、
  Experimental Prototype、Deferred Capability 的当前状态矩阵。
- 修正 `SKILL.md` 对 F11–F13 的状态描述：正式实现位于 `runtime/`，
  `experimental/` 仅保留历史原型，Docker Sandbox 仍为 `DEFERRED`。
- 修正 `docs/MANAGED_RUNTIME_ARCHITECTURE.md` 的分层说明，明确 F11、F12、F13
  已进入正式 Runtime 路径。
- 为旧的未就绪评审增加历史快照标记，避免把历史结论误读为当前状态。
- 更新 Runtime 文档测试，锁定当前状态矩阵并递归检查正式 Runtime 不导入实验模块。

## Tests

- `python -m pytest -q tests/test_runtime_documentation.py`：4 passed
- `python -m pytest -q tests/test_runtime_documentation.py tests/test_runtime_policy.py tests/test_formal_context_builder.py`：16 passed
- `git diff --check`：PASS

## Regression

- 未修改 `runtime/`、`config/` 中的 F10–F13 实现。
- 三 Agent 边界、project.yaml 权威投影、CAS / Lease、Secret Boundary 与 Docker
  `DEFERRED` 约束保持不变。

## Known limitations

- Browser Acceptance Harness、Feature Completeness 和 Calibration 尚未实现。
- Docker Sandbox 仍不可用；本阶段不以 Docker 为前置条件。
- 仓库存在历史 pytest 临时目录，未删除；按规则保留并在最终清理审计时处理。

## Architecture impact

- 仅修正文档状态和文档测试，没有新增 Agent、修改安全边界或改变 Runtime 协议。
- `experimental/` 与正式 `runtime/` 的边界更加明确，后续 Stage 可在正式基础设施上增量扩展。

## Next stage

- Stage 1：设计并实现受 `Role → Capability Policy → Browser Policy → Browser Harness`
  链路约束的确定性 Browser Acceptance Harness。
