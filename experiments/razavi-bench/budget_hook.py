#!/usr/bin/env python3
"""Claude Code PreToolUse hook: refuse notebook tool calls beyond a per-run budget.

run_agent.py installs it for the notebook arm through --settings. Claude Code sends the pending
tool call as JSON on stdin; exit code 2 refuses the call and shows stderr to the model. The
state file counts the calls of one run: {"calls": {tool: n}, "refused": n}.

    budget_hook.py --state <file> --total 10 --ask 2
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys

PREFIX = "mcp__silicon-notebook__"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--total", type=int, default=0, help="notebook calls allowed in one run; 0 = no limit")
    parser.add_argument("--ask", type=int, default=0, help="`ask` calls allowed in one run; 0 = no limit")
    args = parser.parse_args()
    try:
        name = str(json.load(sys.stdin).get("tool_name") or "")
    except (json.JSONDecodeError, AttributeError):
        return 0
    if not name.startswith(PREFIX):
        return 0
    tool = name[len(PREFIX):]
    with open(args.state, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)  # several calls of one turn arrive at once
        handle.seek(0)
        try:
            state = json.loads(handle.read() or "{}")
        except json.JSONDecodeError:
            state = {}
        calls = state.setdefault("calls", {})
        used = sum(calls.values())
        if args.total and used >= args.total:
            reason = f"all {args.total} notebook calls allowed for this question are used"
        elif tool == "ask" and args.ask and calls.get("ask", 0) >= args.ask:
            reason = (f"both `ask` calls are used" if args.ask == 2 else f"all {args.ask} `ask` calls are used")
            reason += f"; {args.total - used} other notebook calls remain" if args.total else ""
        else:
            reason = ""
            calls[tool] = calls.get(tool, 0) + 1
        if reason:
            state["refused"] = state.get("refused", 0) + 1
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(state))
    if reason:
        print(f"Refused: {reason}. Work with what you already have and write answer.md.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
