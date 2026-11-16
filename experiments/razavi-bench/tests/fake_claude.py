#!/usr/bin/env python3
"""A stand-in for `claude -p --output-format stream-json`, for the offline tests.

Reads the prompt on stdin, prints a transcript shaped like Claude Code's, and writes answer.md
in the current directory. FAKE_CLAUDE picks the behaviour:

    (unset)        read the figure if there is one, query the notebook if it is configured, answer
    lazy_once      the first call skips the figure; later calls behave
    disconnected   the notebook server is reported as failed
    final_only     answer in the final message, with no answer.md
    no_notebook    never query the notebook

FAKE_CLAUDE_LOG, when set, gets one JSON line per call: argv, prompt and working directory.
"""

import json
import os
import sys
from pathlib import Path

argv, prompt, cwd = sys.argv[1:], sys.stdin.read(), Path.cwd()
mode = os.environ.get("FAKE_CLAUDE", "")
log = os.environ.get("FAKE_CLAUDE_LOG")
calls = 0
if log:
    calls = len(Path(log).read_text().splitlines()) if Path(log).exists() else 0
    with open(log, "a") as handle:
        handle.write(json.dumps({"argv": argv, "prompt": prompt, "cwd": str(cwd)}) + "\n")

config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())
server = next(iter(config["mcpServers"]), None)
connected = bool(server) and mode != "disconnected"
tools = argv[argv.index("--tools") + 1].split(",")
if connected:
    tools += [f"mcp__{server}__{name}" for name in ("search", "ask", "read_reference", "list_notebooks", "get_notebook")]


def emit(event):
    print(json.dumps(event), flush=True)


def use(name, arguments, result):
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": name, "input": arguments}]}})
    emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": result}]}})


emit({"type": "system", "subtype": "init", "model": argv[argv.index("--model") + 1], "tools": tools,
      "mcp_servers": [{"name": server, "status": "connected" if connected else "failed"}] if server else []})
if (cwd / "figure-1.png").exists() and not (mode == "lazy_once" and calls == 0):
    use("Read", {"file_path": str(cwd / "figure-1.png")}, [{"type": "image"}])
if connected and "list_notebooks" in prompt:  # the connection probe
    use(f"mcp__{server}__list_notebooks", {}, [{"type": "text", "text": "one notebook"}])
    use(f"mcp__{server}__get_notebook", {}, [{"type": "text", "text": "12 sources"}])
elif connected and mode != "no_notebook":
    use(f"mcp__{server}__ask", {"question": "q", "output": "evidence"},
        [{"type": "text", "text": "source_title: A Textbook; text: gm = 2 ID / Vov"}])
answer = "The gain doubles."
if "UNREADABLE" in prompt:  # the isolation probe: its own notes.txt, and a file in another directory
    import re
    other = Path(re.findall(r"`(/[^`]+)`", prompt)[0])
    denied = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"] if "--settings" in argv else []
    blocked = any(str(other).startswith(rule[len("Read(/"):-len("**)")]) for rule in denied)
    emit({"type": "result", "subtype": "success", "is_error": False,
          "result": (cwd / "notes.txt").read_text() + ("UNREADABLE" if blocked else other.read_text())})
    sys.exit(0)
if mode != "final_only":
    (cwd / "answer.md").write_text(answer + "\n")
emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 10,
      "result": answer if mode == "final_only" else "Done.", "usage": {"input_tokens": 1, "output_tokens": 1}})
