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


def test_f11_f12_f13_not_marked_complete() -> None:
    for filename in (
        "F11_EXECUTION_ENVIRONMENT_REPORT.md",
        "F12_SECURITY_BOUNDARY_REPORT.md",
        "F13_CONTEXT_BUILDER_REPORT.md",
    ):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert "EXPERIMENTAL PROTOTYPE" in text
        assert "不属于正式 Runtime" in text
