#!/usr/bin/env python3
"""What each attempt of a run did, call by call: when, how long, how much came back.

    timeline.py --out <run directory> [--arm notebook] [--task-slug <slug>]

For every attempt in <out>/<arm>/runs.jsonl it prints the state of the notebook connection at
the start, then one line per tool call: the second it was issued, how long the model took to
issue it (since the previous result), how long the call ran, the size of what came back, and
the text of an error. Arguments are shown in brief; what the tools returned is never printed.

Call times come from the file the runner writes beside each transcript. For a transcript from
before that file existed they come from Claude Code's own session log, when it is still there.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
from typing import Any, Iterator

PREFIX = "mcp__silicon-notebook__"


def json_lines(path: Path) -> Iterator[Any]:
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            yield json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            yield None


def session_log(session_id: str | None) -> Path | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "projects"
    found = sorted(root.glob(f"*/{session_id}.jsonl")) if session_id and root.is_dir() else []
    return found[0] if found else None


def timed_events(transcript: Path) -> tuple[list[tuple[float | None, dict]], str]:
    """The transcript's events with the second each happened, and where the times came from."""
    events = list(json_lines(transcript))
    times_file = transcript.with_suffix(".times.json")
    if times_file.exists():
        times = json.loads(times_file.read_text())
        return [(times[i] if i < len(times) else None, e) for i, e in enumerate(events) if isinstance(e, dict)], "runner"
    session_id = next((e.get("session_id") for e in events if isinstance(e, dict) and e.get("session_id")), None)
    log = session_log(session_id)
    if log:
        stamped = []
        for event in json_lines(log):
            if isinstance(event, dict) and isinstance(event.get("timestamp"), str) and event.get("type") in ("assistant", "user"):
                try:
                    stamped.append((datetime.datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).timestamp(), event))
                except ValueError:
                    pass
        if stamped:
            start = stamped[0][0]
            return [(round(at - start, 1), event) for at, event in stamped], "session log"
    return [(None, e) for e in events if isinstance(e, dict)], "none"


def brief(name: str, arguments: dict, cwd: str | None) -> str:
    cut = lambda value, n: " ".join(str(value).split())[:n]
    if name == "Read":
        path = str(arguments.get("file_path") or "")
        outside = bool(cwd) and path.startswith("/") and not path.startswith(cwd.rstrip("/") + "/")
        return path + ("   <-- OUTSIDE the run directory" if outside else "")
    if name == "Bash":
        return cut(arguments.get("command"), 120)
    if name == "Write":
        return str(arguments.get("file_path") or "")
    parts = [f"{key}={cut(arguments[key], 20)}" for key in ("output", "mode") if key in arguments]
    parts += [f"{key}={cut(value, 70)!r}" for key, value in arguments.items() if key not in ("output", "mode")][:2]
    return ", ".join(parts)


def error_text(content: Any) -> str:
    if isinstance(content, list):
        content = " ".join(str(block.get("text") or "") for block in content if isinstance(block, dict))
    return " ".join(str(content).split())[:220]


def show(row: dict, transcript: Path) -> None:
    print(f"\n== {row['task_slug'][:9]} r{row['rollout']} attempt {row['attempt']} | {row.get('status')} | "
          f"{row.get('wall_s')} s | answer {row.get('answer_chars', 0)} chars")
    if not transcript.exists():
        print("   (no transcript)")
        return
    init = next((e for e in json_lines(transcript) if isinstance(e, dict) and e.get("type") == "system" and e.get("subtype") == "init"), {})
    cwd = init.get("cwd")
    offered = [str(tool) for tool in init.get("tools") or []]
    servers = ", ".join(f"{s.get('name')}={s.get('status')}" for s in init.get("mcp_servers") or [] if isinstance(s, dict))
    print(f"   at start: notebook server {servers or 'not configured'} | tools offered: "
          f"{sorted(t for t in offered if not t.startswith(PREFIX))} + {sum(t.startswith(PREFIX) for t in offered)} notebook tools")
    events, source = timed_events(transcript)
    print(f"   call times from: {source}")
    calls: dict[Any, dict] = {}
    last = 0.0
    for at, event in events:
        content = (event.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                calls[block.get("id")] = {"name": name.removeprefix(PREFIX), "args": brief(name, block.get("input") or {}, cwd),
                                          "at": at, "think": None if at is None else round(at - last, 1)}
            elif block.get("type") == "tool_result":
                call = calls.pop(block.get("tool_use_id"), None)
                if call is None:
                    continue
                ran = None if at is None or call["at"] is None else round(at - call["at"], 1)
                size = len(json.dumps(block.get("content"), ensure_ascii=False))
                outcome = "ERROR: " + error_text(block.get("content")) if block.get("is_error") else "ok"
                fmt = lambda value: "    ?" if value is None else f"{value:7.1f}"
                print(f"   +{fmt(call['at'])}s  think{fmt(call['think'])}s  ran{fmt(ran)}s  {size:>9,} chars  "
                      f"{call['name']}({call['args']})  {outcome}")
                if at is not None:
                    last = at
    for call in calls.values():
        print(f"   +{call['at']}s  {call['name']}({call['args']})  no result (the run ended first)")
    result = next((e for _, e in events if e.get("type") == "result"), None) or \
        next((e for e in json_lines(transcript) if isinstance(e, dict) and e.get("type") == "result"), None)
    if result:
        usage = result.get("usage") or {}
        print(f"   end: {result.get('subtype')} | turns {result.get('num_turns')} | model time "
              f"{round((result.get('duration_api_ms') or 0) / 1000)} s of {round((result.get('duration_ms') or 0) / 1000)} s | "
              f"tokens in {usage.get('input_tokens')} (+{usage.get('cache_read_input_tokens')} cached), out {usage.get('output_tokens')}")
        if result.get("is_error"):
            print("   end error: " + error_text(result.get("result")))
    else:
        print("   end: no result event (killed or crashed)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", action="append", choices=("control", "notebook"))
    parser.add_argument("--task-slug", action="append")
    parser.add_argument("--limit", type=int, default=0, help="only the first N attempts of each arm")
    args = parser.parse_args()
    for arm in args.arm or ["control", "notebook"]:
        runs = args.out / arm / "runs.jsonl"
        rows = [row for row in json_lines(runs) if isinstance(row, dict)] if runs.exists() else []
        if args.task_slug:
            rows = [row for row in rows if row.get("task_slug") in args.task_slug]
        print(f"\n######## {arm}: {len(rows)} attempts")
        for row in rows[: args.limit or None]:
            if row.get("transcript"):
                show(row, args.out / arm / "transcripts" / str(row["transcript"]))
            else:
                print(f"\n== {row['task_slug'][:9]} r{row['rollout']} attempt {row['attempt']} | {row.get('status')} (claude was not started)")


if __name__ == "__main__":
    main()
