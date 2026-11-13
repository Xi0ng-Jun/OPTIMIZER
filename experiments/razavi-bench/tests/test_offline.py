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
sys.path.insert(0, str(ROOT))
import probe  # noqa: E402

SLUGS = ("part1-001-secret-diode-load", "part2-001-secret-ldo")


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


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

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_arm(self, arm: str, mode: str = "", *extra: str) -> str:
        env = {**os.environ, "FAKE_CLAUDE": mode, "FAKE_CLAUDE_LOG": str(self.log),
               "ANTHROPIC_BASE_URL": "http://unused", "SILICON_NOTEBOOK_AGENT_TOKEN": "t",
               "SILICON_NOTEBOOK_MCP_URL": "http://unused/mcp"}
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
        self.assertTrue(all("silicon-notebook" not in c["prompt"] for c in control))
        runs = rows(self.out / "notebook" / "runs.jsonl")
        self.assertTrue(all(r["notebook_calls"] == 1 and r["ask_not_evidence"] == 0 and not r["reads_outside_workdir"] for r in runs))
        self.assertEqual({r["task_slug"]: r["figure_read"] for r in runs}, {SLUGS[0]: True, SLUGS[1]: False})

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
        done = subprocess.run([sys.executable, str(ROOT / "summarize.py"), "--bench", str(self.bench), "--out", str(self.out),
                               "--leak-pattern", "a textbook", "--draws", "200"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        summary = json.loads((self.out / "summary.json").read_text())
        self.assertEqual([summary["scores"]["control"][p] for p in ("all", "part1", "part2")], [50.0, 50.0, 50.0])
        self.assertEqual([summary["scores"]["notebook"][p] for p in ("all", "part1", "part2")], [75.0, 100.0, 50.0])
        self.assertEqual(summary["paired"]["all"]["difference"], 25.0)
        self.assertEqual((summary["paired"]["part1"]["difference"], summary["paired"]["part1"]["better"]), (50.0, 1))
        self.assertEqual(summary["usage"]["notebook"]["runs_that_called_the_notebook"], 4)
        self.assertEqual(summary["leak"]["runs_matching"], 4)  # every fake notebook run retrieved "A Textbook"
        self.assertEqual(summary["leak"]["scores_without_them"]["graded"], 0)


if __name__ == "__main__":
    unittest.main()
