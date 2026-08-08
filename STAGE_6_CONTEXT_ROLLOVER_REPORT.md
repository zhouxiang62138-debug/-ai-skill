# Stage 6 Context Rollover 报告

## 结果

**PASS（定向验证）**

## 已完成

- 保留 F13 Context Builder 与现有 Durable Manifest/Delta/Resume，新增独立 Model Invocation
  生命周期，不新增 Agent。
- 新增配置驱动的预算、Tool Call、Compaction、长运行和阶段/角色转换触发规则。
- 新增结构化 Handoff、F10 SQLite 持久化、追加式保护、Secret/大小/项目引用边界。
- Fresh Invocation 必须基于已 rollover 的旧 Invocation，重新调用 F13 Builder 并携带
  Handoff 与当前 Durable 状态。
- 事件只写入 Invocation/Handoff 引用和摘要元数据，不写入完整 Context 正文。

## 定向证据

- `python -B -m pytest -q tests/test_context_rollover.py`：5 passed。
- `python -B -m pytest -q tests/test_context_resume.py tests/test_formal_context_builder.py tests/test_session_store.py`：21 passed。
- `git diff --check`：通过；仅有 Git 的换行格式提示。

## 未在本阶段完成的验证

尚未重新运行全量回归；将在 Stage 7/8 完成后统一执行。
