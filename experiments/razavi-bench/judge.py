#!/usr/bin/env python3
"""Grade answers with the benchmark's own judge script, through the endpoint Claude Code uses.

The benchmark's tools/evaluate_answers.py speaks OpenAI's API formats. This runs that script
unchanged (its judge prompt, rubric, parsing, retries, resume and output files) and replaces
only the HTTP call with an Anthropic-format one, so the judge is reached the way Claude Code
reaches its models:

    RAZAVI_JUDGE_MODEL                                 the judge model
    RAZAVI_JUDGE_BASE_URL, RAZAVI_JUDGE_AUTH_TOKEN     the API group that serves it; when unset,
                                                       ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN

    judge.py --bench <razavi-bench> --input answers.jsonl --output scores.jsonl [--resume] [--limit N]

Any other option of evaluate_answers.py may follow and is passed through.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def messages_call(*, api_url: str, api_key: str, model: str, prompt: str, timeout: int,
                  max_tokens: int, temperature: float | None, system: str, **_: Any) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "system": system,
                               "messages": [{"role": "user", "content": prompt}]}
    if temperature is not None:
        payload["temperature"] = temperature
    request = urllib.request.Request(
        api_url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The stock script records only the exception's text: put the server's reason in it.
        raise ValueError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    blocks = body.get("content") if isinstance(body, dict) else None
    text = "".join(block.get("text") or "" for block in blocks or [] if isinstance(block, dict) and block.get("type") == "text")
    if not text.strip():
        raise ValueError(f"no text in the judge's response (stop_reason={body.get('stop_reason') if isinstance(body, dict) else None})")
    return text, body


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench", type=Path, required=True, help="local clone of razavi-bench")
    args, rest = parser.parse_known_args()
    own = bool(os.environ.get("RAZAVI_JUDGE_BASE_URL"))  # the judge's own API group, as a pair
    base_env, key_env = ("RAZAVI_JUDGE_BASE_URL", "RAZAVI_JUDGE_AUTH_TOKEN") if own else ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN")
    base, model = os.environ.get(base_env, ""), os.environ.get("RAZAVI_JUDGE_MODEL", "")
    missing = [name for name, value in ((base_env, base), (key_env, os.environ.get(key_env)), ("RAZAVI_JUDGE_MODEL", model)) if not value]
    if missing:
        raise SystemExit("missing environment: " + ", ".join(missing))
    script = args.bench.resolve() / "tools" / "evaluate_answers.py"
    spec = importlib.util.spec_from_file_location("razavi_evaluate_answers", script)
    stock = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stock)
    stock.call_judge_once = lambda **kwargs: messages_call(system=stock.JUDGE_SYSTEM, **kwargs)
    sys.argv = [str(script), "--repo", str(args.bench.resolve()),
                "--api-url", base.rstrip("/") + "/v1/messages", "--api-key-env", key_env,
                "--model", model, "--max-tokens", "16000", "--timeout", "600", *rest]
    stock.main()


if __name__ == "__main__":
    main()
