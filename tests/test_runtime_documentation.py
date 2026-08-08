from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_schema_version_is_consistent() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    schema = (ROOT / "docs" / "project_state_schema.md").read_text(encoding="utf-8")
    assert "project schema v7" in skill
    assert "新项目使用 `schema_version: 6`" not in schema
    assert "control_plane_id" in schema
    assert "last_event_sequence" not in schema
    assert "last_checkpoint_id" not in schema


def test_f11_f12_historical_reports_remain_marked_experimental() -> None:
    for filename in (
        "F11_EXECUTION_ENVIRONMENT_REPORT.md",
        "F12_SECURITY_BOUNDARY_REPORT.md",
    ):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert "EXPERIMENTAL PROTOTYPE" in text
        assert "不属于正式 Runtime" in text


def test_current_runtime_status_distinguishes_formal_experimental_and_deferred() -> None:
    status = (ROOT / "docs" / "RUNTIME_CAPABILITY_STATUS.md").read_text(
        encoding="utf-8"
    )
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs" / "MANAGED_RUNTIME_ARCHITECTURE.md").read_text(
        encoding="utf-8"
    )

    assert "## Formal Production Runtime" in status
    assert "## Experimental Prototype" in status
    assert "## Deferred Capability" in status
    assert "F11 Execution Runtime" in status
    assert "F12 Managed Security Runtime" in status
    assert "F13 Deterministic Context Runtime" in status
    assert "Docker Sandbox / 物理进程隔离" in status
    assert "F11–F13 已移入 `experimental/`" not in skill
    assert "F11、F12、F13 已在正式 Runtime 路径中增量接入" in architecture


def test_browser_profile_and_policy_are_declared() -> None:
    profile = (ROOT / "config" / "evaluation_rules" / "web_app.yaml").read_text(
        encoding="utf-8"
    )
    policy = (ROOT / "config" / "browser_policy.yaml").read_text(encoding="utf-8")
    role_policy = (ROOT / "config" / "role_policies.yaml").read_text(encoding="utf-8")
    assert "browser_validation:" in profile
    assert "required: true" in profile
    assert "GATE-BROWSER-ACCEPTANCE" in profile
    assert "feature_completeness:" in profile
    assert "GATE-FEATURE-COMPLETENESS" in profile
    assert "capability: browser.access" in policy
    assert "default: deny" in policy
    assert "- browser.access" in role_policy


def test_official_runtime_does_not_import_experimental_modules() -> None:
    for source in (ROOT / "runtime").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "experimental." not in text
        assert "experimental/" not in text


def test_planner_generator_strategy_protocol_is_declared() -> None:
    strategy_schema = ROOT / "config" / "schemas" / "implementation_strategy_v1.schema.json"
    strategy_template = ROOT / "templates" / "implementation_strategy.yaml"
    strategy_script = ROOT / "scripts" / "implementation_strategy.py"
    planner = (ROOT / "prompts" / "planner_prompt.md").read_text(encoding="utf-8")
    generator = (ROOT / "prompts" / "generator_prompt.md").read_text(encoding="utf-8")
    policy = (ROOT / "config" / "role_policies.yaml").read_text(encoding="utf-8")
    assert strategy_schema.is_file()
    assert strategy_template.is_file()
    assert strategy_script.is_file()
    assert "planner_what_why" in planner
    assert "generator_how" in generator
    assert "memory/handoffs/" in policy


def test_conditional_contract_protocol_is_declared() -> None:
    contract_config = ROOT / "config" / "implementation_contract.yaml"
    contract_schema = ROOT / "config" / "schemas" / "implementation_contract_v1.schema.json"
    contract_script = ROOT / "scripts" / "implementation_contract.py"
    planner = (ROOT / "prompts" / "planner_prompt.md").read_text(encoding="utf-8")
    generator = (ROOT / "prompts" / "generator_prompt.md").read_text(encoding="utf-8")
    evaluator = (ROOT / "prompts" / "evaluator_prompt.md").read_text(encoding="utf-8")
    assert contract_config.is_file()
    assert contract_schema.is_file()
    assert contract_script.is_file()
    assert "Conditional Implementation Contract" in generator
    assert "不是新的用户批准门" in generator
    assert "Contract 只是 Generator/Evaluator 的执行约定" in evaluator
    assert "风险事实" in planner


def test_context_rollover_protocol_is_declared() -> None:
    context_config = (ROOT / "config" / "context.yaml").read_text(encoding="utf-8")
    rollover_schema = ROOT / "config" / "schemas" / "rollover_handoff_v1.schema.json"
    rollover_module = ROOT / "runtime" / "context" / "rollover.py"
    workflow = (ROOT / "docs" / "workflow_protocol.md").read_text(encoding="utf-8")
    status = (ROOT / "docs" / "RUNTIME_CAPABILITY_STATUS.md").read_text(encoding="utf-8")
    assert "rollover:" in context_config
    assert rollover_schema.is_file()
    assert rollover_module.is_file()
    assert "Fresh Invocation" in workflow
    assert "Rollover/Fresh Invocation" in status


def test_best_candidate_protocol_is_declared() -> None:
    candidate_config = ROOT / "config" / "candidate_policy.yaml"
    candidate_schema = ROOT / "config" / "schemas" / "candidate_v1.schema.json"
    candidate_script = ROOT / "scripts" / "best_candidate.py"
    runtime_service = ROOT / "runtime" / "candidates.py"
    evaluator = (ROOT / "prompts" / "evaluator_prompt.md").read_text(encoding="utf-8")
    assert candidate_config.is_file()
    assert candidate_schema.is_file()
    assert candidate_script.is_file()
    assert runtime_service.is_file()
    assert "Best Validated Candidate" in evaluator
    assert "no_restore_performed: true" in evaluator
