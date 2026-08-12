"""F10 Runtime 命令行入口。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .orchestrator import Orchestrator


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(type(value).__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Development Team Durable Runtime")
    parser.add_argument("action", choices=("begin-step", "commit-step", "fail-step", "start", "resume", "inspect", "inspect-step", "pause", "recover", "run-phase"))
    parser.add_argument("target", help="start 时为项目根目录，其余为 Session ID")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--worker-id")
    parser.add_argument("--run-id")
    parser.add_argument("--lease-token")
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    root = Path(args.target) if args.action in {"start", "begin-step", "run-phase"} else args.project_root
    if root is None:
        parser.error("非 start 命令必须提供 --project-root")
    orchestrator = Orchestrator(root)
    if args.action in {"start", "begin-step"}:
        result = orchestrator.start(worker_id=args.worker_id)
    elif args.action == "run-phase":
        raise SystemExit(
            "run-phase 只允许宿主注入 ModelInvocationAdapter 后调用 Orchestrator.run_phase；CLI 不接受任意模型 Callable。"
        )
    elif args.action == "commit-step":
        if args.run_id is None or args.lease_token is None or args.result is None:
            parser.error("commit-step 必须提供 --run-id、--lease-token 和 --result")
        result = orchestrator.commit_step(args.target, args.run_id, args.lease_token, json.loads(args.result.read_text(encoding="utf-8")))
    elif args.action == "fail-step":
        if args.run_id is None or args.result is None:
            parser.error("fail-step 必须提供 --run-id 和 --result")
        orchestrator.fail_step(args.target, args.run_id, json.loads(args.result.read_text(encoding="utf-8")))
        result = {"result": "FAILED", "run_id": args.run_id}
    elif args.action == "resume":
        result = orchestrator.resume(args.target)
    elif args.action in {"inspect", "inspect-step"}:
        result = orchestrator.inspect(args.target)
    elif args.action == "pause":
        orchestrator.pause(args.target)
        result = {"result": "PAUSED", "session_id": args.target}
    else:
        result = orchestrator.recover_session(
            args.target, worker_id=args.worker_id
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
