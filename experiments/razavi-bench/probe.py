#!/usr/bin/env python3
"""Checks to pass before any Razavi-Bench run.

Through `claude`, with the run's own settings (these need --model):

    probe.py image      does an image the agent reads reach the model? A PNG with a random
                        six-digit number is generated and the model must read the number back.
    probe.py mcp        is the silicon-notebook MCP server connected, and does a read succeed?
    probe.py leak       does the notebook hold Razavi's two articles the questions come from?
                        Their text states the reference verdicts, so a run that retrieves them
                        is not a test of the other material.
    probe.py isolation  does the rule that keeps the agent out of the benchmark clone hold? The
                        agent is asked to read a file in a denied directory.

Straight to the notebook's MCP endpoint, without `claude`:

    probe.py ping       does the endpoint answer, and what do `ask` and `search` take?
    probe.py latency    how long do `search` and `ask` (evidence only, both modes) take on one
                        textbook question, and how much text comes back? Sizes only are printed.

Exit code 0 means the check passed (`leak` and `latency` always exit 0: read their report).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path

import mcp_client
import run_agent

LATENCY_QUESTION = ("How does source degeneration change the transconductance and the output resistance "
                    "of a common-source stage?")

#   aaa
#  f   b
#   ggg
#  e   c
#   ddd
SEGMENTS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
            "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg"}
BOXES = {"a": (0, 0, 60, 10), "g": (0, 45, 60, 55), "d": (0, 90, 60, 100),
         "f": (0, 0, 10, 50), "b": (50, 0, 60, 50), "e": (0, 50, 10, 100), "c": (50, 50, 60, 100)}


def number_png(code: str, scale: int = 2) -> bytes:
    """A white PNG with `code` drawn as seven-segment digits."""
    cell, margin = 80, 30
    width, height = margin * 2 + cell * len(code), margin * 2 + 100
    pixels = [bytearray(b"\xff" * width) for _ in range(height)]
    for index, digit in enumerate(code):
        left = margin + index * cell
        for segment in SEGMENTS[digit]:
            x0, y0, x1, y1 = BOXES[segment]
            for y in range(margin + y0, margin + y1):
                pixels[y][left + x0:left + x1] = b"\x00" * (x1 - x0)
    rows = b"".join(b"\x00" + bytes(v for v in row for _ in range(scale)) for row in pixels for _ in range(scale))
    chunk = lambda kind, data: (struct.pack(">I", len(data)) + kind + data
                                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    header = struct.pack(">IIBBBBB", width * scale, height * scale, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def call(args: argparse.Namespace, arm: str, prompt: str, files: dict[str, bytes] | None = None,
         deny: list[Path] | None = None) -> dict:
    if not args.model:
        raise SystemExit("this check runs `claude`: pass --model")
    env = run_agent.child_env(args)
    run_agent.check_env(env, arm)
    with tempfile.TemporaryDirectory(prefix="rb-probe-") as name:
        workdir = Path(name)
        for filename, data in (files or {}).items():
            (workdir / filename).write_bytes(data)
        ran = run_agent.run_claude(run_agent.claude_command(args, arm, tools="Read", deny=deny), prompt, workdir, env, args.timeout)
    info = run_agent.parse_stream(ran["stdout"], ran["times"])
    if not info["init"]:
        print("no transcript from claude; exit code", ran["exit_code"], "\n" + ran["stderr"][-800:])
        raise SystemExit(2)
    print("model:", info["init"].get("model"))
    print("tool calls:", [use["name"] for use in info["tool_uses"]] or "none")
    return info


def final_text(info: dict) -> str:
    return str(info["result"].get("result") or "")


def six_digits() -> str:
    return "".join(random.SystemRandom().choice("0123456789") for _ in range(6))


def probe_image(args: argparse.Namespace) -> int:
    code = six_digits()
    info = call(args, "control",
                "Open `code.png` in the current directory with the Read tool. It shows a six-digit "
                "number. Reply with the six digits and nothing else.", {"code.png": number_png(code)})
    reply = final_text(info)
    digits = "".join(ch for ch in reply if ch.isdigit())
    print("shown:", code, "| read back:", reply.strip()[:120])
    passed = digits == code
    print("PASS: the image reaches the model" if passed else "FAIL: the model did not read the number")
    return 0 if passed else 1


def probe_mcp(args: argparse.Namespace) -> int:
    info = call(args, "notebook",
                "Call the silicon-notebook tool `list_notebooks`, then `get_notebook` for the default "
                "notebook. Reply with one line per notebook (its name, and whether it is the default), "
                "then one line with what `get_notebook` says about how many sources it holds.")
    servers = info["init"].get("mcp_servers")
    prefix = f"mcp__{run_agent.MCP_SERVER}__"
    offered = sorted(str(t).removeprefix(prefix) for t in info["init"].get("tools") or [] if str(t).startswith(prefix))
    called = {use["name"] for use in info["tool_uses"]}
    print("servers:", servers)
    print("notebook tools offered:", offered or "none")
    print("reply:\n" + final_text(info).strip())
    passed = (run_agent.mcp_connected(info["init"])
              and {prefix + "list_notebooks", prefix + "get_notebook"} <= called
              and not info["result"].get("is_error"))
    print("PASS: connected, and both calls were made" if passed else "FAIL: see above")
    write_tools = sorted(set(offered) & {"add_source", "delete_source", "reparse_source", "build",
                                         "create_notebook", "propose_memory", "add_observation",
                                         "put_knowhow_cell_code"})
    if write_tools:
        print("NOTE: this token also offers write tools:", write_tools, "- a read + ask token is enough")
    return 0 if passed else 1


def probe_leak(args: argparse.Namespace) -> int:
    info = call(args, "notebook",
                "Using the silicon-notebook tools (for example `find_sources`, `list_sources`, `search`), "
                "check whether the default notebook holds either of these two articles by Behzad Razavi "
                "from the column \"The Analog Mind\" in IEEE Solid-State Circuits Magazine: \"Analog "
                "Design Experiments With AI - Part 1\" (vol. 17, no. 4, Fall 2025) and \"Analog Design "
                "Experiments With AI - Part 2\" (vol. 18, no. 2, Spring 2026). Look by title and by "
                "author. Reply with the title of every source that is one of the two articles, or with "
                "`NONE FOUND`; then one line naming the searches you made.")
    print("reply:\n" + final_text(info).strip())
    return 0


def probe_isolation(args: argparse.Namespace) -> int:
    inside, outside = six_digits(), six_digits()
    with tempfile.TemporaryDirectory(prefix="rb-denied-") as denied:
        secret = Path(denied) / "notes.txt"
        secret.write_text(f"The number is {outside}.\n", encoding="utf-8")
        prompt = ("Read two files with the Read tool: `notes.txt` in the current directory, and "
                  f"`{secret}`. Reply with the number each file contains, or UNREADABLE for a file you "
                  "cannot read.")
        files = {"notes.txt": f"The number is {inside}.\n".encode()}
        print("--- without a rule")
        free = final_text(call(args, "control", prompt, files))
        print("--- with the rule that denies the other directory")
        ruled = final_text(call(args, "control", prompt, files, deny=[Path(denied)]))
    print("without a rule, a file outside the run directory is", "readable" if outside in free else "not readable")
    print("with the rule: own file", "read" if inside in ruled else "NOT read", "| denied file",
          "READ" if outside in ruled else "not read")
    passed = inside in ruled and outside not in ruled
    print("PASS: the rule keeps the agent out of a denied directory" if passed else "FAIL: see above")
    return 0 if passed else 1


def endpoint(args: argparse.Namespace) -> tuple[str, str]:
    url = os.environ.get(run_agent.URL_ENV, "")
    token = os.environ.get(run_agent.TOKEN_ENV, "")
    if not token and args.token_file:
        token = Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
    if not url or not token:
        raise SystemExit(f"missing environment: {run_agent.URL_ENV} or {run_agent.TOKEN_ENV}")
    return url, token


def signature(tool: dict) -> str:
    schema = tool.get("inputSchema") or {}
    required = set(schema.get("required") or [])
    return f"{tool.get('name')}(" + ", ".join(name + ("*" if name in required else "") for name in schema.get("properties") or {}) + ")"


def probe_ping(args: argparse.Namespace) -> int:
    got = mcp_client.ping(*endpoint(args), timeout=60)
    if not got["ok"]:
        print(f"FAIL: no answer after {got['seconds']} s: {got['error']}")
        return 1
    tools = {tool.get("name"): tool for tool in got["tools"]}
    print(f"answered in {got['seconds']} s | server: {got['server']} | {len(tools)} tools")
    for name in ("ask", "search", "read_reference"):
        print("  " + (signature(tools[name]) if name in tools else f"{name}: NOT OFFERED"), " (* = required)")
    print("PASS: the endpoint answers a direct connection")
    return 0


def shape(text: str) -> str:
    """What a tool returned, without its content: size, and the top-level fields of a JSON object."""
    out = f"{len(text):,} chars (~{len(text) // 4:,} tokens)"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return out
    if isinstance(value, dict):
        fields = [f"{key}[{len(item)}]" if isinstance(item, list) else key for key, item in value.items()]
        out += " | " + (f"status={value['status']} | " if isinstance(value.get("status"), str) else "") + "fields: " + ", ".join(fields)
    return out


def probe_latency(args: argparse.Namespace) -> int:
    url, token = endpoint(args)
    client = mcp_client.McpClient(url, token, timeout=60)
    try:
        client.initialize()
        tools = {tool.get("name"): tool for tool in client.list_tools()}
        search_arg = ((tools.get("search") or {}).get("inputSchema") or {}).get("required") or ["query"]
        calls = [("search", {search_arg[0]: LATENCY_QUESTION}),
                 ("ask", {"question": LATENCY_QUESTION, "output": "evidence", "mode": "chunk"}),
                 ("ask", {"question": LATENCY_QUESTION, "output": "evidence", "mode": "reasoning"})]
        for name, arguments in calls:
            label = name + (f" (evidence, {arguments['mode']})" if name == "ask" else "")
            started = time.time()
            try:
                result = client.call(name, arguments, timeout=args.timeout)
            except mcp_client.McpError as exc:
                print(f"{label}: FAILED after {time.time() - started:.1f} s: {exc}", flush=True)
                continue
            text = "".join(block.get("text") or "" for block in result.get("content") or [] if isinstance(block, dict))
            verdict = "tool error: " + " ".join(text.split())[:300] if result.get("isError") else shape(text)
            print(f"{label}: {time.time() - started:.1f} s | {verdict}", flush=True)
        concurrent_calls(url, token, search_arg[0], args.timeout)
    except mcp_client.McpError as exc:
        print(f"could not connect: {exc}")
    finally:
        client.close()
    return 0


def concurrent_calls(url: str, token: str, search_arg: str, timeout: float) -> None:
    """Whether the service works on several calls at once: decides how many runs may go in parallel."""
    def timed(name: str, arguments: dict) -> str:
        client = mcp_client.McpClient(url, token, timeout=60)
        started = time.time()
        try:
            client.initialize()
            result = client.call(name, arguments, timeout=timeout)
            return f"{time.time() - started:.1f} s" + (" (tool error)" if result.get("isError") else "")
        except mcp_client.McpError as exc:
            return f"FAILED after {time.time() - started:.1f} s: {exc}"
        finally:
            client.close()

    ask = {"question": LATENCY_QUESTION, "output": "evidence", "mode": "reasoning"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        print("--- at the same time, each on its own connection", flush=True)
        asks = [pool.submit(timed, "ask", ask) for _ in range(2)]
        time.sleep(5)
        search = pool.submit(timed, "search", {search_arg: LATENCY_QUESTION})
        connect = pool.submit(mcp_client.ping, url, token, 60)
        print(f"search while two asks run: {search.result()}", flush=True)
        got = connect.result()
        print("new connection while two asks run: " + (f"{got['seconds']} s" if got["ok"] else f"FAILED: {got['error']}"), flush=True)
        print("two asks at once: " + " | ".join(future.result() for future in asks), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=("image", "mcp", "leak", "isolation", "ping", "latency"))
    parser.add_argument("--model", default="")
    parser.add_argument("--small-model", default="")
    parser.add_argument("--token-file", default="")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--mcp-tool-timeout-ms", type=int, default=900_000)
    parser.add_argument("--mcp-output-tokens", type=int, default=50_000)
    args = parser.parse_args()
    checks = {"image": probe_image, "mcp": probe_mcp, "leak": probe_leak, "isolation": probe_isolation,
              "ping": probe_ping, "latency": probe_latency}
    sys.exit(checks[args.check](args))


if __name__ == "__main__":
    main()
