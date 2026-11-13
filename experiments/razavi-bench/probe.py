#!/usr/bin/env python3
"""Checks to pass before any Razavi-Bench run, each one `claude` call with the run's own settings.

    probe.py image   does an image the agent reads reach the model? A PNG with a random six-digit
                     number is generated and the model must read the number back.
    probe.py mcp     is the silicon-notebook MCP server connected, and does a read succeed?
    probe.py leak    does the notebook hold Razavi's two articles the questions come from?
                     Their text states the reference verdicts, so a run that retrieves them is
                     not a test of the other material.

Exit code 0 means the check passed (`leak` always exits 0: read its report).
"""

from __future__ import annotations

import argparse
import random
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import run_agent

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


def call(args: argparse.Namespace, arm: str, prompt: str, files: dict[str, bytes] | None = None) -> dict:
    env = run_agent.child_env(args)
    run_agent.check_env(env, arm)
    with tempfile.TemporaryDirectory(prefix="rb-probe-") as name:
        workdir = Path(name)
        for filename, data in (files or {}).items():
            (workdir / filename).write_bytes(data)
        ran = run_agent.run_claude(run_agent.claude_command(args, arm, tools="Read"), prompt, workdir, env, args.timeout)
    info = run_agent.parse_stream(ran["stdout"])
    if not info["init"]:
        print("no transcript from claude; exit code", ran["exit_code"], "\n" + ran["stderr"][-800:])
        raise SystemExit(2)
    print("model:", info["init"].get("model"))
    print("tool calls:", [use["name"] for use in info["tool_uses"]] or "none")
    return info


def final_text(info: dict) -> str:
    return str(info["result"].get("result") or "")


def probe_image(args: argparse.Namespace) -> int:
    code = "".join(random.SystemRandom().choice("0123456789") for _ in range(6))
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("check", choices=("image", "mcp", "leak"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--small-model", default="")
    parser.add_argument("--token-file", default="")
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--mcp-tool-timeout-ms", type=int, default=900_000)
    parser.add_argument("--mcp-output-tokens", type=int, default=50_000)
    args = parser.parse_args()
    sys.exit({"image": probe_image, "mcp": probe_mcp, "leak": probe_leak}[args.check](args))


if __name__ == "__main__":
    main()
