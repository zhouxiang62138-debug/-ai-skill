# F13 EXPERIMENTAL PROTOTYPE：上下文构建报告

已实现 `build_context(session_id, role, checkpoint_id, relevant_artifacts, relevant_issue_ids, token_budget)`。
它按角色、路径和预算读取原始工件片段，保留截断标记与 Event hash 引用；Generator 被
确定性禁止读取候选产品方案和设计预览，Evaluator 仍需依赖证据而非 Generator 文本结论。

F13 没有启用同角色多 Worker、任务图或并行执行；这些扩展应在独立并发设计后再启用。
