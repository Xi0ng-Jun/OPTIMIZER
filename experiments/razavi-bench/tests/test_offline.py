"""Offline tests of the runner and the summary, against a made-up two-task benchmark and a fake `claude`.

    python3 -m unittest discover -s experiments/razavi-bench/tests
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path[:0] = [str(ROOT), str(HERE)]
import fake_mcp  # noqa: E402
import mcp_client  # noqa: E402
import probe  # noqa: E402
import run_agent  # noqa: E402

SLUGS = ("part1-001-secret-diode-load", "part2-001-secret-ldo")


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().split("\n") if line.strip()] if path.exists() else []


class Offline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.bench, self.out, self.work, self.log = base / "bench", base / "out", base / "work", base / "calls.jsonl"
        self.work.mkdir()
        (self.bench / "data").mkdir(parents=True)
        tasks = []
        for slug, figure in zip(SLUGS, (True, False)):
            (self.bench / "tasks" / slug).mkdir(parents=True)
            figures = []
            if figure:
                (self.bench / "tasks" / slug / "figure-07.png").write_bytes(probe.number_png("12"))
                figures = [f"tasks/{slug}/figure-07.png"]
            tasks.append({"task_slug": slug, "part": slug[:5], "task_path": f"tasks/{slug}",
                          "prompt": "What happens to the gain?", "figures": figures})
        (self.bench / "data" / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
        self.mcp, self.mcp_url = fake_mcp.start()
        self.addCleanup(self.mcp.server_close)
        self.addCleanup(self.mcp.shutdown)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_arm(self, arm: str, mode: str = "", *extra: str) -> str:
        env = {**os.environ, "FAKE_CLAUDE": mode, "FAKE_CLAUDE_LOG": str(self.log), "no_proxy": "127.0.0.1",
               "ANTHROPIC_BASE_URL": "http://unused", "SILICON_NOTEBOOK_AGENT_TOKEN": fake_mcp.TOKEN,
               "SILICON_NOTEBOOK_MCP_URL": self.mcp_url}
        done = subprocess.run(
            [sys.executable, str(ROOT / "run_agent.py"), "--bench", str(self.bench), "--arm", arm,
             "--out", str(self.out), "--model", "test-model", "--rollout", "1", "--rollout", "2",
             "--concurrency", "1", "--work-root", str(self.work),
             "--claude-bin", str(HERE / "fake_claude.py"), *extra],
            env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_both_arms_answer_and_differ_only_in_the_notebook(self) -> None:
        self.run_arm("control")
        self.run_arm("notebook")
        for arm in ("control", "notebook"):
            answers = rows(self.out / arm / "answers.jsonl")
            self.assertEqual({(a["task_slug"], a["rollout"]) for a in answers}, {(s, r) for s in SLUGS for r in (1, 2)})
            self.assertTrue(all(a["answer"] == "The gain doubles." and a["task_path"] and a["model_name"] == f"test-model__{arm}" for a in answers))
            self.assertTrue(all(r["status"] == "ok" and r["accepted"] for r in rows(self.out / arm / "runs.jsonl")))
        calls = rows(self.log)
        for call in calls:  # the task's name never reaches the agent, and its directory is removed
            self.assertFalse(any(slug in call["prompt"] or slug in call["cwd"] for slug in SLUGS))
            self.assertFalse(Path(call["cwd"]).exists())
        control, notebook = calls[:4], calls[4:]
        self.assertTrue(all(c["argv"][c["argv"].index("--mcp-config") + 1].endswith("mcp.empty.json") and "--allowedTools" not in c["argv"] for c in control))
        self.assertTrue(all("mcp__silicon-notebook" in c["argv"] and "silicon-notebook" in c["prompt"] for c in notebook))
        for call in calls:  # every call carries the rule that keeps the agent out of the benchmark and the outputs
            denied = json.loads(call["argv"][call["argv"].index("--settings") + 1])["permissions"]["deny"]
            self.assertTrue({f"Read(/{self.bench.resolve()}/**)", f"Read(/{self.out.resolve()}/**)"} <= set(denied))
        self.assertTrue(all("silicon-notebook" not in c["prompt"] for c in control))
        runs = rows(self.out / "notebook" / "runs.jsonl")
        self.assertTrue(all(r["notebook_calls"] == 1 and r["ask_not_evidence"] == 0 and not r["reads_outside_workdir"] for r in runs))
        self.assertEqual({r["task_slug"]: r["figure_read"] for r in runs}, {SLUGS[0]: True, SLUGS[1]: False})
        self.assertTrue(all(r["mcp_status"] == "connected" and r["notebook_wait_s"] is not None for r in runs))
        asked = [c for r in runs for c in r["timeline"] if c["tool"] == "ask"]
        self.assertTrue(len(asked) == 4 and all(c["s"] is not None and c["s"] >= 0 and c["error"] is False for c in asked))
        times = json.loads((self.out / "notebook" / "transcripts" / runs[0]["transcript"]).with_suffix(".times.json").read_text())
        self.assertEqual(len(times), len((self.out / "notebook" / "transcripts" / runs[0]["transcript"]).read_text().split("\n")) - 1)
        shown = subprocess.run([sys.executable, str(ROOT / "timeline.py"), "--out", str(self.out)], capture_output=True, text=True)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertIn("notebook server silicon-notebook=connected", shown.stdout)
        self.assertIn("ask(output=evidence", shown.stdout)
        self.assertNotIn("A Textbook", shown.stdout)  # what a tool returned is never printed

    def test_the_notebook_arm_carries_a_call_budget_and_the_hook_enforces_it(self) -> None:
        self.run_arm("control")
        self.run_arm("notebook", "", "--notebook-calls", "3", "--ask-calls", "1")
        control, notebook = rows(self.log)[:4], rows(self.log)[4:]
        settings = lambda call: json.loads(call["argv"][call["argv"].index("--settings") + 1])
        self.assertTrue(all("hooks" not in settings(c) and "notebook calls" not in c["prompt"] for c in control))
        self.assertTrue(all("at most 3 notebook calls" in c["prompt"] and "at most 1 `ask`" in c["prompt"] for c in notebook))
        hook = settings(notebook[0])["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "mcp__silicon-notebook__.*")
        self.assertTrue(all(r["budget"] == {"total": 3, "ask": 1} and r["budget_refused"] == 0 for r in rows(self.out / "notebook" / "runs.jsonl")))
        # The hook itself, as Claude Code calls it: the command from the settings, the tool call on stdin.
        command = hook["hooks"][0]["command"]
        state = Path(command.split("--state ")[1].split(" --total")[0])
        self.addCleanup(lambda: state.unlink(missing_ok=True))
        def fire(tool: str) -> tuple[int, str]:
            done = subprocess.run(command, shell=True, input=json.dumps({"tool_name": tool}), capture_output=True, text=True)
            return done.returncode, done.stderr
        nb = "mcp__silicon-notebook__"
        self.assertEqual([fire(t)[0] for t in ("Read", nb + "ask", nb + "ask", nb + "search", nb + "search", nb + "search")], [0, 0, 2, 0, 0, 2])
        self.assertIn("write answer.md", fire(nb + "read_reference")[1])
        self.assertEqual(json.loads(state.read_text()), {"calls": {"ask": 1, "search": 2}, "refused": 3})
        self.out = self.out.parent / "unlimited"
        self.run_arm("notebook", "", "--notebook-calls", "0")
        last = rows(self.log)[-1]
        self.assertTrue("hooks" not in settings(last) and "notebook calls" not in last["prompt"])

    def test_a_notebook_that_stays_down_costs_no_model_call(self) -> None:
        self.mcp.state["fail_after"] = 1  # up for the runner's first check, then down
        out = self.run_arm("notebook", "", "--notebook-wait", "0", "--max-attempts", "2", "--task-slug", SLUGS[1], "--rollout", "1")
        runs = rows(self.out / "notebook" / "runs.jsonl")
        self.assertEqual([(r["status"], r["accepted"]) for r in runs if r["rollout"] == 1], [("notebook_down", False)] * 2)
        self.assertEqual(rows(self.log), [])
        self.assertIn("WARNING", out)

    def test_the_client_follows_the_redirect_and_reads_both_answer_forms(self) -> None:
        got = mcp_client.ping(self.mcp_url, fake_mcp.TOKEN, 10)
        self.assertEqual((got["ok"], got["server"], [t["name"] for t in got["tools"]]), (True, "fake notebook", ["search", "ask"]))
        client = mcp_client.McpClient(self.mcp_url, fake_mcp.TOKEN, 10)
        client.initialize()
        result = client.call("ask", {"question": "q", "output": "evidence"})
        self.assertEqual(json.loads(result["content"][0]["text"])["echo"], {"question": "q", "output": "evidence"})
        self.assertTrue(client.url.endswith("/mcp/"))
        sent = self.mcp.state["requests"][-1]
        self.assertEqual((sent["auth"], sent["session"], sent["method"]), ("Bearer t", "s1", "tools/call"))
        self.assertFalse(mcp_client.ping(self.mcp_url, "wrong", 10)["ok"])

    def test_the_isolation_probe_tells_a_rule_that_holds_from_one_that_does_not(self) -> None:
        env = {**os.environ, "ANTHROPIC_BASE_URL": "http://unused", "FAKE_CLAUDE": ""}
        command = [sys.executable, str(ROOT / "probe.py"), "isolation", "--model", "m", "--claude-bin", str(HERE / "fake_claude.py")]
        done = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("without a rule, a file outside the run directory is readable", done.stdout)
        self.assertIn("PASS", done.stdout)
        ignoring = Path(self.tmp.name) / "claude_ignoring_rules.py"  # a claude on which the rule has no effect
        ignoring.write_text("#!/usr/bin/env python3\nimport runpy, sys\ni = sys.argv.index('--settings') if '--settings' in sys.argv else None\n"
                            "if i is not None:\n    del sys.argv[i:i + 2]\n"
                            f"runpy.run_path({str(HERE / 'fake_claude.py')!r}, run_name='__main__')\n")
        ignoring.chmod(0o755)
        done = subprocess.run([*command[:-1], str(ignoring)], env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("denied file READ", done.stdout)

    def test_a_line_separator_inside_a_string_does_not_split_an_event(self) -> None:
        line = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a", "content": "x\u2028y"}]}}, ensure_ascii=False)
        use = json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a", "name": "Read", "input": {}}]}})
        info = run_agent.parse_stream(use + "\n" + line + "\n", [1.0, 3.5])
        self.assertEqual([(u["at"], u["seconds"], u["error"]) for u in info["tool_uses"]], [(1.0, 2.5, False)])

    def test_a_run_that_skips_the_figure_is_sampled_again(self) -> None:
        self.run_arm("control", "lazy_once", "--task-slug", SLUGS[0], "--rollout", "1")
        runs = [r for r in rows(self.out / "control" / "runs.jsonl") if r["rollout"] == 1]
        self.assertEqual([(r["status"], r["accepted"]) for r in runs], [("figure_not_read", False), ("ok", True)])
        self.assertEqual(sum(a["rollout"] == 1 for a in rows(self.out / "control" / "answers.jsonl")), 1)

    def test_a_disconnected_notebook_yields_no_answer(self) -> None:
        out = self.run_arm("notebook", "disconnected", "--max-attempts", "2")
        self.assertEqual(rows(self.out / "notebook" / "answers.jsonl"), [])
        self.assertTrue(all(r["status"] == "mcp_not_connected" for r in rows(self.out / "notebook" / "runs.jsonl")))
        self.assertIn("WARNING", out)

    def test_an_unused_notebook_is_graded_as_returned_and_marked(self) -> None:
        self.run_arm("notebook", "no_notebook", "--max-attempts", "2", "--task-slug", SLUGS[1])
        runs = rows(self.out / "notebook" / "runs.jsonl")
        self.assertEqual([(r["attempt"], r["status"], r["accepted"]) for r in runs if r["rollout"] == 1],
                         [(1, "notebook_not_used", False), (2, "notebook_not_used", True)])
        self.assertEqual(len(rows(self.out / "notebook" / "answers.jsonl")), 2)

    def test_final_message_is_the_fallback_and_resume_skips_answered_runs(self) -> None:
        self.run_arm("control", "final_only")
        self.assertTrue(all(r["answer_source"] == "final_message" for r in rows(self.out / "control" / "runs.jsonl")))
        self.assertIn("0 to run, 4 already answered", self.run_arm("control"))

    def test_summary(self) -> None:
        self.run_arm("control")
        self.run_arm("notebook")
        given = {"control": {SLUGS[0]: (2, 2), SLUGS[1]: (4, 0)}, "notebook": {SLUGS[0]: (4, 4), SLUGS[1]: (2, 2)}}
        for arm, table in given.items():
            (self.out / arm / "scores.jsonl").write_text("".join(
                json.dumps({"task_slug": slug, "score_0_to_4": score, "answer_metadata": {"rollout": rollout}}) + "\n"
                for slug, pair in table.items() for rollout, score in zip((1, 2), pair)))
        with (self.out / "control" / "runs.jsonl").open("a") as handle:  # a slot given up after three attempts
            handle.write(json.dumps({"task_slug": SLUGS[0], "rollout": 3, "attempt": 3, "status": "no_answer", "accepted": False}) + "\n")
        done = subprocess.run([sys.executable, str(ROOT / "summarize.py"), "--bench", str(self.bench), "--out", str(self.out),
                               "--leak-pattern", "a textbook", "--draws", "200"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual([summary["scores"]["control"][p] for p in ("all", "part1", "part2")], [50.0, 50.0, 50.0])
        self.assertEqual([summary["scores"]["notebook"][p] for p in ("all", "part1", "part2")], [75.0, 100.0, 50.0])
        self.assertEqual((summary["scores"]["control"]["unanswered"], summary["scores"]["control"]["all_unanswered_0"]), (1, 40.0))
        self.assertEqual((summary["scores"]["notebook"]["unanswered"], summary["scores"]["notebook"]["all_unanswered_0"]), (0, 75.0))
        self.assertEqual(summary["paired"]["all"]["difference"], 25.0)
        self.assertEqual((summary["paired"]["part1"]["difference"], summary["paired"]["part1"]["better"]), (50.0, 1))
        self.assertEqual(summary["usage"]["notebook"]["runs_that_called_the_notebook"], 4)
        self.assertEqual(summary["leak"]["runs_matching"], 4)  # every fake notebook run retrieved "A Textbook"
        self.assertEqual(summary["leak"]["scores_without_them"]["graded"], 0)


if __name__ == "__main__":
    unittest.main()
