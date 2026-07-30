# 返工记录 <编号>

```yaml
schema_version: 1
record_reference: memory/handoffs/rework-<nnn>.md
failure_class: implementation_issue
target_role: generator
retry_number: 1
source_evaluation: evaluation/reports/evaluation-<nnn>.md
required_evidence: artifacts/evidence/evidence-<nnn>.yaml
summary: <可复现的问题摘要>
affected_artifacts: []
```

返工记录必须追加创建。`retry_number` 必须等于当前 `current_iteration + 1`，
同一记录不得重复计数。
