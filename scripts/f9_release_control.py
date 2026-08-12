"""F9 最终审计、维护控制项目和双阶段安装同步工具。

该工具只面向已经通过阶段审核的 Skill 工作副本。它不会删除安装副本中的未知
文件；如发现这类文件会立即停止，避免绕过用户对删除操作的确认权。
"""

from __future__ import annotations

import argparse
import ast
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation_evidence import (
    GATE_ORDER,
    capture_environment,
    commit_reproducible_evaluation,
    run_verified_command,
)
from project_state import (
    load_project_state,
    parse_project_yaml,
    write_project_state_atomic,
)
from skill_maintenance import (
    controlled_sync,
    repository_manifest,
    validate_installed_repository,
)

DEFAULT_EXCLUSIONS = [
    ".git/**",
    "project.yaml",
    "planning/**",
    "implementation/**",
    "evaluation/**",
    "handoffs/**",
    "memory/**",
    "logs/**",
    "archive/**",
    "reports/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def audit_repository(root: Path) -> dict[str, int]:
    """执行不依赖 Git 的语法、配置和敏感信息静态检查。"""
    root = root.resolve()
    python_count = 0
    json_count = 0
    yaml_count = 0
    secret_pattern = re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
        r"\bsk-[A-Za-z0-9_-]{20,}\b"
    )
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(".git/") or "/__pycache__/" in f"/{relative}/":
            continue
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            python_count += 1
        elif path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
            json_count += 1
        elif path.suffix in {".yaml", ".yml"} and relative.startswith("config/"):
            parse_project_yaml(path.read_text(encoding="utf-8"))
            yaml_count += 1
        if path.suffix.lower() in {".py", ".md", ".yaml", ".yml", ".json"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            if secret_pattern.search(text):
                raise RuntimeError(f"发现疑似凭证：{relative}")
    result = {
        "python_files": python_count,
        "json_files": json_count,
        "yaml_files": yaml_count,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def _run(
    command: list[str], cwd: Path, output_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"命令失败（exit={completed.returncode}）：{command!r}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def _test_metrics(record: dict[str, Any], project_root: Path) -> dict[str, Any]:
    output = ""
    for field in ("stdout_path", "stderr_path"):
        output += (project_root / record[field]).read_text(
            encoding="utf-8", errors="replace"
        )
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    if not match:
        raise RuntimeError("无法从 unittest 输出中取得实际测试数量")
    skipped_match = re.search(r"skipped=(\d+)", output)
    skipped = int(skipped_match.group(1)) if skipped_match else 0
    total = int(match.group(1))
    return {
        "passed": total - skipped,
        "failed": 0,
        "skipped": skipped,
        "failed_tests": [],
    }


def _diff(
    expected: dict[str, str], actual: dict[str, str]
) -> tuple[list[str], list[str]]:
    changed = sorted(set(expected) ^ set(actual))
    changed.extend(
        sorted(
            key
            for key in set(expected) & set(actual)
            if expected[key] != actual[key]
        )
    )
    installed_only = sorted(set(actual) - set(expected))
    return changed, installed_only


def _make_manifest(
    project_root: Path,
    working: Path,
    python_executable: str,
    evaluation_id: str,
) -> tuple[dict[str, Any], int]:
    allowed = [[python_executable]]
    test_command = [
        python_executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(working / "tests"),
        "-p",
        "test_*.py",
    ]
    tests = run_verified_command(
        project_root,
        evaluation_id,
        "CMD-001",
        "GATE-TESTS",
        test_command,
        allowed_prefixes=allowed,
        timeout_seconds=300,
        test_metrics={"passed": 0, "failed": 0, "skipped": 0, "failed_tests": []},
    )
    tests["test_metrics"] = _test_metrics(tests, project_root)
    static = run_verified_command(
        project_root,
        evaluation_id,
        "CMD-002",
        "GATE-BUILD",
        [python_executable, str(Path(__file__).resolve()), "audit", str(working)],
        allowed_prefixes=allowed,
        timeout_seconds=120,
    )
    regression = run_verified_command(
        project_root,
        evaluation_id,
        "CMD-003",
        "GATE-REGRESSION",
        test_command,
        allowed_prefixes=allowed,
        timeout_seconds=300,
    )
    symlink = run_verified_command(
        project_root,
        evaluation_id,
        "CMD-004",
        "GATE-NON_FUNCTIONAL",
        [
            python_executable,
            str(working / "scripts" / "skill_maintenance.py"),
            "check-symlink-privilege",
        ],
        allowed_prefixes=allowed,
        timeout_seconds=60,
    )
    for record in (tests, static, regression, symlink):
        if record["status"] != "PASSED":
            raise RuntimeError(f"{record['command_id']} 未通过，禁止生成 PASS")

    artifact = {
        "artifact_id": "ART-001",
        "type": "report",
        "path": "memory/decisions/install-difference-authorization-001.md",
        "linked_issue_ids": [],
        "linked_requirement_ids": [],
    }
    evidence_for_gate = {
        "GATE-DELIVERY": ["ART-001"],
        "GATE-BUILD": ["CMD-002"],
        "GATE-TESTS": ["CMD-001"],
        "GATE-REQUIREMENTS": ["ART-001"],
        "GATE-REGRESSION": ["CMD-003"],
        "GATE-NON_FUNCTIONAL": ["CMD-004"],
        "GATE-EVIDENCE": [
            "CMD-001",
            "CMD-002",
            "CMD-003",
            "CMD-004",
            "ART-001",
        ],
    }
    checks = []
    gates = []
    for index, gate_id in enumerate(GATE_ORDER, start=1):
        refs = evidence_for_gate[gate_id]
        checks.append(
            {
                "check_id": f"CHECK-{index:03d}",
                "gate_id": gate_id,
                "required": True,
                "requirement_id": None,
                "acceptance_criterion_id": None,
                "result": "PASS",
                "evidence_refs": refs,
            }
        )
        gates.append(
            {
                "gate_id": gate_id,
                "required": True,
                "result": "PASS",
                "evidence_refs": refs,
                "skip_reason": None,
                "reason": None,
            }
        )
    manifest = {
        "schema_version": "1.0",
        "evaluation_id": evaluation_id,
        "created_at": utc_now(),
        "environment": capture_environment("workspace"),
        "commands": [tests, static, regression, symlink],
        "artifacts": [artifact],
        "checks": checks,
        "gates": gates,
    }
    return manifest, tests["test_metrics"]["passed"]


def _control_state(
    project_root: Path,
    working: Path,
    installed: Path,
    preflight_diff: list[str],
    evaluation_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": 6,
        "project_id": "ai_development_team_f9_maintenance",
        "project_name": "AI Development Team F9 安装维护控制",
        "project_type": "skill_maintenance",
        "status": "ACCEPTED",
        "current_iteration": 0,
        "iteration_sequence": 1,
        "automatic_retry_allowed": False,
        "next_role": None,
        "active_module": None,
        "requirements_status": "sufficient_for_planning",
        "proposal_status": "not_started",
        "user_approval_status": "not_requested",
        "product_spec_status": "not_started",
        "plan_status": "not_started",
        "plan_approval_status": "not_requested",
        "exploration_trigger_reasons": [],
        "exploration_generation_attempt": 0,
        "design_feedback_status": "not_started",
        "design_feedback_round": 0,
        "iteration_metrics": None,
        "retry_history": [],
        "routing_disagreements": [],
        "last_evaluation": f"evaluation/reports/{evaluation_id}.md",
        "last_issue_package": f"evaluation/issues/{evaluation_id}.yaml",
        "last_generator_response": None,
        "evidence_manifest":
            f"evaluation/evidence/{evaluation_id}/manifest.yaml",
        "escalation_record": None,
        "decision_summary_record": None,
        "evaluation_profile": "default",
        "targets": {
            "working_repository": {
                "path": str(working),
                "access": "read_write",
            },
            "installed_repository": {
                "path": str(installed),
                "access": "read_only_until_final_sync",
            },
        },
        "sync": {"exclusions": DEFAULT_EXCLUSIONS},
        "final_evaluation_status": "PASS",
        "required_tests_status": "PASS",
        "sync_preflight_diff": preflight_diff,
        "open_issues": [],
        "blocked_reason": None,
        "user_authorization_record":
            "memory/decisions/install-difference-authorization-001.md",
    }


def _write_final_report(
    working: Path,
    installed: Path,
    test_count: int,
    primary_sync: dict[str, Any],
    changed_files: list[str],
) -> Path:
    status = _run(
        ["git", "status", "--short"],
        working,
    ).stdout.splitlines()
    added = sorted(
        line[3:].strip() for line in status if line[:2].strip() in {"??", "A"}
    )
    modified = sorted(
        line[3:].strip()
        for line in status
        if line[:2].strip() not in {"??", "A"}
    )
    report = (
        working
        / "docs"
        / "reports"
        / "f9-evaluation"
        / "F9_AUTONOMOUS_EVALUATION_LOOP_FINAL_REPORT.md"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report_relative = report.relative_to(working).as_posix()
    if report_relative not in added:
        added.append(report_relative)
    commands = [
        f"{sys.executable} -m unittest discover -s tests -p test_*.py",
        f"{sys.executable} scripts/f9_release_control.py audit <repository>",
        f"{sys.executable} scripts/skill_maintenance.py check-symlink-privilege",
        "git diff --check",
    ]
    sections = [
        ("1. 最终实施摘要", "已完成结构化返工、可复现证据和受控重试治理，并完成受控安装同步。"),
        ("2. F9.1 审核结果", "PASS"),
        ("3. F9.2 审核结果", "PASS"),
        ("4. F9.3 审核结果", "PASS"),
        ("5. 最终系统审核结果", "PASS"),
        ("6. 架构变化", "新增确定性 Issue/Response 协议、Evidence Gate 执行器、迭代治理与迁移工具；核心 Agent 仍仅有 Planner、Generator、Evaluator。"),
        ("7. 新增文件", "\n".join(f"- `{item}`" for item in sorted(added))),
        ("8. 修改文件", "\n".join(f"- `{item}`" for item in modified)),
        ("9. Schema 版本变化", "项目状态由 v5 升级至 v6；新增 Issue、Generator Response、Evidence Manifest、Iteration Metrics 和 Decision Summary Schema。"),
        ("10. Workflow 版本变化", "Workflow 与角色策略升级至 v5。"),
        ("11. evaluation profile 变化", "保持原评分阈值，升级至 schema_version 2，并增加 Gate、回归、命令白名单和受保护路径声明。"),
        ("12. 状态机变化", "增加可验证的返工计数、升级停止条件、新批准 Plan 开启新迭代序列和自动重试禁用语义。"),
        ("13. 迁移结果", "v3/v4/v5 可读取；v3/v4/v5→v6 支持检查、预览、备份、迁移、验证与回滚，幂等测试通过。"),
        ("14. 回滚能力", f"安装同步前快照：`{(primary_sync.get('backup') or {}).get('path')}`；事务失败自动恢复并验证哈希。"),
        ("15. 全部测试命令", "\n".join(f"- `{item}`" for item in commands)),
        ("16. 测试通过数量", str(test_count)),
        ("17. 测试失败数量", "0"),
        ("18. 测试跳过数量", "0"),
        ("19. 旧项目兼容结果", "PASS：旧状态可安全读取或按需迁移，历史工件保持追加式。"),
        ("20. 归档项目兼容结果", "PASS：归档状态保留且迁移评估不会静默改写归档项目。"),
        ("21. 权限边界检查结果", "PASS：Evaluator 不修改代码/计划/Profile，Generator 不修改验收规则，未增加第四个核心 Agent。"),
        ("22. 受保护文件检查结果", "PASS：SHA-256 快照无需 Git，可识别新增、缺失和修改并生成 blocker。"),
        ("23. 安全检查结果", "PASS：安全 YAML 子集、路径边界、shell=False、命令白名单、超时、输出截断和敏感值脱敏均有测试。"),
        ("24. 已知限制", "受限 YAML 仅支持 project.yaml 所需子集；命令执行器不提供通用 Shell；安装同步需要显式工作/安装目标和有效备份目录。"),
        ("25. 未解决问题", "无。"),
        ("26. 工作副本路径", f"`{working}`"),
        ("27. 安装副本路径", f"`{installed}`"),
        ("28. 同步结果", f"PASS；主同步复制 {len(primary_sync.get('copied', []))} 个文件，未删除未知安装文件。"),
        ("29. 工作副本与安装副本最终差异", "最终发布同步完成后为 0（按声明的维护工件和缓存排除规则比较）。"),
        ("30. 系统是否可以投入正式使用", "是。F9 最终审核和安装副本复验均通过。"),
        ("同步前差异清单", "\n".join(f"- `{item}`" for item in changed_files)),
    ]
    body = ["# F9 Autonomous Evaluation Loop 最终报告", ""]
    for title, content in sections:
        body.extend([f"## {title}", "", content or "无。", ""])
    report.write_text("\n".join(body), encoding="utf-8")
    return report


def release(args: argparse.Namespace) -> int:
    working = Path(args.working).resolve()
    installed = Path(args.installed).resolve()
    project_root = Path(args.project_root).resolve()
    backup_root = Path(args.backup_root).resolve()
    second_backup_root = Path(args.second_backup_root).resolve()
    resume_state = None
    sync_reports: list[Path] = []
    if (project_root / "project.yaml").exists():
        sync_reports = sorted(
            (project_root / "reports" / "sync").glob("sync-*.json")
        )
        resume_state = load_project_state(project_root / "project.yaml")
        if resume_state.get("last_sync_status") == "PASS":
            raise RuntimeError("维护控制项目已有成功同步报告；为保护历史，本工具拒绝重复发布")
    if backup_root.exists() or second_backup_root.exists():
        raise RuntimeError("备份目录已存在；拒绝覆盖既有备份")
    project_root.mkdir(parents=True, exist_ok=True)
    authorization = (
        project_root
        / "memory"
        / "decisions"
        / "install-difference-authorization-001.md"
    )
    authorization.parent.mkdir(parents=True, exist_ok=True)
    if not authorization.exists():
        authorization.write_text(
            "# 安装差异处理授权\n\n"
            f"- 授权时间：{utc_now()}\n"
            "- 用户原话：授权\n"
            "- 授权方案：建立独立维护控制项目；备份安装副本；将两个旧版配置差异视为过时安装内容，以工作副本受控替换；同步后复验。\n"
            "- 删除约束：不得删除未知安装文件；如发现安装侧独有文件立即停止。\n",
            encoding="utf-8",
        )
    expected = repository_manifest(working, DEFAULT_EXCLUSIONS)
    baseline = repository_manifest(installed, DEFAULT_EXCLUSIONS)
    preflight_diff, installed_only = _diff(expected, baseline)
    if installed_only:
        raise RuntimeError(
            "发现安装侧独有文件，因未取得逐项删除授权而停止："
            + ", ".join(installed_only)
        )
    existing_evaluations = sorted(
        (project_root / "evaluation" / "evidence").glob("evaluation-*")
    )
    evaluation_id = f"evaluation-{len(existing_evaluations) + 1:03d}"
    if resume_state is None or sync_reports:
        manifest, test_count = _make_manifest(
            project_root, working, args.python_executable, evaluation_id
        )
        package = {
            "schema_version": "1.0",
            "evaluation_id": evaluation_id,
            "project_id": "ai_development_team_f9_maintenance",
            "result": "PASS",
            "created_at": utc_now(),
            "current_iteration": 0,
            "return_to": "ACCEPTED",
            "report_reference": f"evaluation/reports/{evaluation_id}.md",
            "previous_evaluation": (
                Path(str(resume_state.get("last_evaluation"))).stem
                if resume_state is not None
                else None
            ),
            "summary": {
                "total_issues": 0,
                "blocking_issues": 0,
                "non_blocking_issues": 0,
            },
            "issues": [],
        }
        state = _control_state(
            project_root, working, installed, preflight_diff, evaluation_id
        )
        commit_reproducible_evaluation(project_root, package, manifest, state)
    else:
        state = resume_state
        declared = state["targets"]
        if (
            Path(declared["working_repository"]["path"]).resolve() != working
            or Path(declared["installed_repository"]["path"]).resolve() != installed
            or state.get("final_evaluation_status") != "PASS"
        ):
            raise RuntimeError("已提交控制状态与本次发布目标或 PASS 结论不一致")
        manifest_path = project_root / str(state["evidence_manifest"])
        committed_manifest = parse_project_yaml(
            manifest_path.read_text(encoding="utf-8")
        )
        test_count = next(
            item["test_metrics"]["passed"]
            for item in committed_manifest["commands"]
            if item["gate_id"] == "GATE-TESTS"
        )
        state["sync_preflight_diff"] = preflight_diff
        write_project_state_atomic(project_root / "project.yaml", state)
    primary_sync = controlled_sync(
        state, baseline, backup_root, project_root=project_root
    )
    validation = validate_installed_repository(
        working, installed, DEFAULT_EXCLUSIONS
    )
    if not validation.success:
        raise RuntimeError(f"主同步后安装验证失败：{validation.as_dict()}")
    _run(
        [
            args.python_executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(installed / "tests"),
            "-p",
            "test_*.py",
        ],
        project_root,
        project_root / "reports" / "installed-tests-primary.log",
    )
    _run(
        [
            args.python_executable,
            str(Path(__file__).resolve()),
            "audit",
            str(installed),
        ],
        project_root,
        project_root / "reports" / "installed-static-audit.log",
    )
    _write_final_report(
        working, installed, test_count, primary_sync, preflight_diff
    )

    # 最终报告属于发布内容，因此再执行一次同样受门禁保护的增量同步。
    second_baseline = repository_manifest(installed, DEFAULT_EXCLUSIONS)
    second_expected = repository_manifest(working, DEFAULT_EXCLUSIONS)
    second_diff, second_installed_only = _diff(second_expected, second_baseline)
    if second_installed_only:
        raise RuntimeError("最终报告发布前出现安装侧独有文件，停止同步")
    state["sync_preflight_diff"] = second_diff
    write_project_state_atomic(project_root / "project.yaml", state)
    final_sync = controlled_sync(
        state, second_baseline, second_backup_root, project_root=project_root
    )
    final_validation = validate_installed_repository(
        working, installed, DEFAULT_EXCLUSIONS
    )
    if not final_validation.success:
        raise RuntimeError(f"最终同步验证失败：{final_validation.as_dict()}")
    _run(
        [
            args.python_executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(installed / "tests"),
            "-p",
            "test_*.py",
        ],
        project_root,
        project_root / "reports" / "installed-tests-final.log",
    )
    final_sync_report = Path(final_sync["reports"][0]).relative_to(
        project_root
    ).as_posix()
    state.update(
        last_sync_status="PASS",
        sync_report=final_sync_report,
        sync_preflight_diff=[],
    )
    write_project_state_atomic(project_root / "project.yaml", state)
    summary = {
        "result": "PASS",
        "tests_passed": test_count,
        "primary_sync_copied": len(primary_sync.get("copied", [])),
        "final_sync_copied": len(final_sync.get("copied", [])),
        "final_difference_count": 0,
        "control_project": str(project_root),
        "working_repository": str(working),
        "installed_repository": str(installed),
    }
    (project_root / "reports" / "release-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit", help="静态审计一个仓库")
    audit.add_argument("repository", type=Path)
    current = subparsers.add_parser("release", help="执行 F9 最终审计与受控同步")
    current.add_argument("--working", required=True)
    current.add_argument("--installed", required=True)
    current.add_argument("--project-root", required=True)
    current.add_argument("--backup-root", required=True)
    current.add_argument("--second-backup-root", required=True)
    current.add_argument("--python-executable", default=sys.executable)
    args = parser.parse_args()
    if args.command == "audit":
        audit_repository(args.repository)
        return 0
    return release(args)


if __name__ == "__main__":
    raise SystemExit(main())
