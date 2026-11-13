"""judge.py against a local stand-in for an Anthropic-format endpoint.

Needs the benchmark's own judge script, so it runs only when RB_BENCH names a razavi-bench clone:

    RB_BENCH=~/razavi-bench python3 -m unittest discover -s experiments/razavi-bench/tests
"""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = Path(os.environ.get("RB_BENCH", "")).expanduser()
seen: list[dict] = []


class Endpoint(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
        if len(seen) == 1:  # the first call fails: the judge must retry it
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"busy")
            return
        reply = {"content": [{"type": "thinking", "thinking": "{\"score_0_to_4\": 0}"},
                             {"type": "text", "text": "{\"score_0_to_4\": 3, \"rationale\": \"mostly right\"}"}],
                 "stop_reason": "end_turn", "usage": {"input_tokens": 5, "output_tokens": 7}}
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


@unittest.skipUnless((BENCH / "tools" / "evaluate_answers.py").is_file(), "set RB_BENCH to a razavi-bench clone")
class Judge(unittest.TestCase):
    def test_scores_through_an_anthropic_format_endpoint(self) -> None:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        tasks = [json.loads(line) for line in (BENCH / "data" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()[:2]]
        with tempfile.TemporaryDirectory() as tmp:
            answers, scores = Path(tmp) / "answers.jsonl", Path(tmp) / "scores.jsonl"
            answers.write_text("".join(json.dumps({"task_slug": t["task_slug"], "task_path": t["task_path"], "rollout": 1,
                                                   "model_name": "m__control", "answer": "It doubles."}) + "\n" for t in tasks))
            # The judge's own API group wins over the one the answering runs use.
            env = {**os.environ, "ANTHROPIC_BASE_URL": "http://127.0.0.1:9/", "ANTHROPIC_AUTH_TOKEN": "other-group",
                   "RAZAVI_JUDGE_BASE_URL": f"http://127.0.0.1:{server.server_port}/",
                   "RAZAVI_JUDGE_AUTH_TOKEN": "secret", "RAZAVI_JUDGE_MODEL": "judge-model", "no_proxy": "127.0.0.1"}
            done = subprocess.run([sys.executable, str(ROOT / "judge.py"), "--bench", str(BENCH), "--input", str(answers),
                                   "--output", str(scores), "--concurrency", "1"], env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            rows = [json.loads(line) for line in scores.read_text().splitlines()]
        self.assertEqual([(r["score_0_to_4"], bool(r["parse_error"]), r["answer_metadata"]["rollout"]) for r in rows], [(3, False, 1)] * 2)
        self.assertEqual(len(seen), 3)
        call = seen[-1]
        self.assertEqual((call["path"], call["auth"], call["body"]["model"]), ("/v1/messages", "Bearer secret", "judge-model"))
        self.assertIn("fair analog-circuit grading judge", call["body"]["system"])
        self.assertIn("<golden_solution>", call["body"]["messages"][0]["content"])
        self.assertIn("It doubles.", call["body"]["messages"][0]["content"])


if __name__ == "__main__":
    unittest.main()
