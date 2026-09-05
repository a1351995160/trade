"""Autonomous Research Orchestrator V2 command line entry point."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .research_factory.autonomous_orchestrator_v2 import AutonomousResearchOrchestratorV2


DEFAULT_OBJECTIVE_ID = "RESEARCH_OBJECTIVE_SHORT_HORIZON_A_SHARE_V1"


def _configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            continue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m chanlun_trader.research_orchestrator", description="自主量化研究编排器 V2")
    parser.add_argument("command", choices=("start", "watch", "status", "pause", "resume", "stop", "recover", "governance-status"))
    parser.add_argument("--root", default=".")
    parser.add_argument("--objective-id", default=DEFAULT_OBJECTIVE_ID)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--watch-timeout-seconds", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="输出 canonical English machine JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_output()
    args = build_parser().parse_args(argv)
    orchestrator = AutonomousResearchOrchestratorV2(args.root, objective_id=args.objective_id)
    if args.command == "start":
        payload = orchestrator.run(max_steps=args.max_steps)
        if str(payload.get("orchestrator_state")) == "AI_MANUAL_HANDOFF_REQUIRED":
            payload = orchestrator.watch_manual_result(poll_seconds=args.poll_seconds, timeout_seconds=args.watch_timeout_seconds)
    elif args.command == "watch":
        payload = orchestrator.watch_manual_result(poll_seconds=args.poll_seconds, timeout_seconds=args.watch_timeout_seconds)
    elif args.command == "status":
        payload = orchestrator.status().to_dict()
    elif args.command in {"pause", "stop"}:
        payload = orchestrator.request(args.command.upper())
    elif args.command == "resume":
        payload = orchestrator.resume()
    elif args.command == "recover":
        payload = orchestrator.recover()
    else:
        payload = orchestrator.governance_status()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
