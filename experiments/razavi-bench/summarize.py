#!/usr/bin/env python3
"""Compare the control and notebook arms of a Razavi-Bench run.

Reads, for each arm under <out>/<arm>/: scores.jsonl (the judge's output for that arm's
answers.jsonl), runs.jsonl and transcripts/. Prints, and writes to <out>/summary.md and
summary.json:

    scores      each arm's score in percent (mean 0-4 score / 4), overall and per part
    difference  notebook minus control, paired by task (a task's score is the mean of its
                rollouts), with a bootstrap 95% interval over tasks
    usage       how the notebook arm used the notebook, and anything a run did that it should not
    leak        with --leak-pattern, the notebook runs whose retrieved text matches the pattern
                (e.g. the title of the articles the questions come from), and the scores again
                without those runs
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
from collections import Counter
from pathlib import Path

ARMS = ("control", "notebook")
PARTS = ("all", "part1", "part2", "figure", "text-only")  # the last two: has a figure or not


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def percent(scores: list[float]) -> float | None:
    return round(100 * sum(scores) / (4 * len(scores)), 2) if scores else None


def load_scores(path: Path) -> dict[tuple[str, int], int]:
    scores = {}
    for row in rows(path):
        rollout = (row.get("answer_metadata") or {}).get("rollout")
        if row.get("score_0_to_4") is not None and rollout is not None:
            scores[(row["task_slug"], int(rollout))] = row["score_0_to_4"]
    return scores


def given_up(run_rows: list[dict]) -> set[tuple[str, int]]:
    """The (task, rollout) slots whose three attempts all ended without an answer."""
    answered = {(row["task_slug"], int(row["rollout"])) for row in run_rows if row.get("accepted")}
    return {(row["task_slug"], int(row["rollout"])) for row in run_rows
            if not row.get("accepted") and int(row.get("attempt") or 0) >= 3} - answered


def arm_table(scores: dict[tuple[str, int], int], part_of: dict[str, str], unanswered: int = 0) -> dict:
    chosen = lambda part: [s for (slug, _), s in scores.items() if part in part_of[slug]]
    rollouts = sorted({rollout for _, rollout in scores})
    return {"graded": len(scores), "unanswered": unanswered,
            "all_unanswered_0": percent(list(scores.values()) + [0] * unanswered),
            **{part: percent(chosen(part)) for part in PARTS},
            "by_rollout": {r: percent([s for (_, k), s in scores.items() if k == r]) for r in rollouts}}


def task_means(scores: dict[tuple[str, int], int]) -> dict[str, float]:
    grouped: dict[str, list[int]] = {}
    for (slug, _), score in scores.items():
        grouped.setdefault(slug, []).append(score)
    return {slug: statistics.mean(values) for slug, values in grouped.items()}


def paired(control: dict, notebook: dict, part_of: dict[str, str], draws: int) -> dict:
    a, b = task_means(control), task_means(notebook)
    out = {}
    for part in PARTS:
        diffs = [25 * (b[slug] - a[slug]) for slug in sorted(a.keys() & b.keys()) if part in part_of[slug]]
        if not diffs:
            out[part] = None
            continue
        rng = random.Random(0)
        means = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(draws))
        out[part] = {"tasks": len(diffs), "difference": round(statistics.mean(diffs), 2),
                     "ci95": [round(means[int(0.025 * draws)], 2), round(means[int(0.975 * draws) - 1], 2)],
                     "better": sum(d > 0 for d in diffs), "same": sum(d == 0 for d in diffs),
                     "worse": sum(d < 0 for d in diffs)}
    changes = sorted(((25 * (b[s] - a[s]), s) for s in a.keys() & b.keys()), reverse=True)
    out["largest_gains"] = [[s, round(d, 1)] for d, s in changes[:5] if d > 0]
    out["largest_losses"] = [[s, round(d, 1)] for d, s in changes[::-1][:5] if d < 0]
    return out


def usage(run_rows: list[dict]) -> dict:
    accepted = [row for row in run_rows if row.get("accepted")]
    tools = Counter()
    for row in accepted:
        tools.update(row.get("tools") or {})
    calls = [row.get("notebook_calls") or 0 for row in accepted]
    tool_seconds: dict[str, list[float]] = {}
    for row in accepted:
        for call in row.get("timeline") or []:
            if call.get("s") is not None:
                tool_seconds.setdefault(call["tool"], []).append(call["s"])
    return {
        "answers": len(accepted),
        "attempts": len(run_rows),
        "attempt_statuses": dict(Counter(row.get("status") for row in run_rows)),
        "accepted_statuses": dict(Counter(row.get("status") for row in accepted)),
        "answer_from_final_message": sum(row.get("answer_source") == "final_message" for row in accepted),
        "tool_calls": dict(tools),
        "runs_that_called_the_notebook": sum(c > 0 for c in calls),
        "median_notebook_calls": statistics.median(calls) if calls else None,
        "call_budget": next((row["budget"] for row in accepted if row.get("budget")), None),
        "runs_with_a_call_refused_by_the_budget": sum(bool(row.get("budget_refused")) for row in accepted),
        "ask_calls_without_output_evidence": sum(row.get("ask_not_evidence") or 0 for row in accepted),
        "runs_reading_a_saved_tool_result": sum(bool(row.get("saved_tool_results_read")) for row in accepted),
        "runs_reading_outside_their_directory": sum(bool(row.get("reads_outside_workdir")) for row in accepted),
        "median_wall_s": statistics.median([row.get("wall_s") or 0 for row in accepted]) if accepted else None,
        "median_seconds_per_call": {tool: statistics.median(values) for tool, values in sorted(tool_seconds.items())},
        "waited_for_notebook_s": round(sum(row.get("notebook_wait_s") or 0 for row in run_rows), 1),
    }


def retrieved_text(transcript: Path) -> str:
    """Everything the tools returned in one run."""
    parts = []
    for line in transcript.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (event.get("message") or {}).get("content") if isinstance(event, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                parts.append(json.dumps(block.get("content"), ensure_ascii=False))
    return "\n".join(parts)


def leaked_runs(arm_dir: Path, pattern: str) -> set[tuple[str, int]]:
    regex = re.compile(pattern, re.IGNORECASE)
    hits = set()
    for row in rows(arm_dir / "runs.jsonl"):
        transcript = arm_dir / "transcripts" / str(row.get("transcript"))
        if row.get("accepted") and transcript.exists() and regex.search(retrieved_text(transcript)):
            hits.add((row["task_slug"], int(row["rollout"])))
    return hits


def fmt(value: float | None, signed: bool = False) -> str:
    return "-" if value is None else (f"{value:+.1f}" if signed else f"{value:.1f}")


def score_lines(title: str, tables: dict[str, dict], diff: dict) -> list[str]:
    lines = [f"### {title}", "", "| | overall | part 1 | part 2 | with figure | text only | answers graded | by rollout | unanswered | overall, unanswered as 0 |",
             "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|"]
    for arm in ARMS:
        t = tables[arm]
        lines.append(f"| {arm} | {fmt(t['all'])} | {fmt(t['part1'])} | {fmt(t['part2'])} | {fmt(t['figure'])} | {fmt(t['text-only'])} | {t['graded']} | "
                     + ", ".join(fmt(v) for v in t["by_rollout"].values()) + f" | {t['unanswered']} | {fmt(t['all_unanswered_0'])} |")
    lines += ["", "Unanswered: all three attempts ended without an answer. Such a slot is left out of every score here except "
              "the last column, which counts it as 0 the way the benchmark's leaderboard does."]
    lines += ["", "| notebook - control | difference | 95% interval | tasks better / same / worse |", "|---|---:|---|---|"]
    for part in PARTS:
        d = diff.get(part)
        if d:
            lines.append(f"| {part} ({d['tasks']} tasks) | {fmt(d['difference'], True)} | "
                         f"{fmt(d['ci95'][0], True)} to {fmt(d['ci95'][1], True)} | "
                         f"{d['better']} / {d['same']} / {d['worse']} |")
    return lines + [""]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--leak-pattern", default="", help="regular expression, case-insensitive")
    parser.add_argument("--draws", type=int, default=10000)
    args = parser.parse_args()

    tasks = rows(args.bench / "data" / "tasks.jsonl")
    part_of = {task["task_slug"]: {"all", task["part"], "figure" if task.get("figures") else "text-only"} for task in tasks}
    scores = {arm: {k: v for k, v in load_scores(args.out / arm / "scores.jsonl").items() if k[0] in part_of} for arm in ARMS}
    lost = {arm: len({key for key in given_up(rows(args.out / arm / "runs.jsonl")) if key[0] in part_of}) for arm in ARMS}
    summary: dict = {
        "tasks": len(tasks),
        "scores": {arm: arm_table(scores[arm], part_of, lost[arm]) for arm in ARMS},
        "paired": paired(scores["control"], scores["notebook"], part_of, args.draws),
        "usage": {arm: usage(rows(args.out / arm / "runs.jsonl")) for arm in ARMS},
    }
    lines = ["## Razavi-Bench: notebook against control", ""]
    lines += score_lines("Scores (percent)", summary["scores"], summary["paired"])
    for label, key in (("Largest gains", "largest_gains"), ("Largest losses", "largest_losses")):
        if summary["paired"][key]:
            lines.append(f"{label}: " + "; ".join(f"{slug} ({fmt(d, True)})" for slug, d in summary["paired"][key]))
    # How much an arm differs from itself between two rollouts: the yardstick for the difference above.
    summary["between_rollouts"] = {}
    for arm in ARMS:
        rollouts = sorted({rollout for _, rollout in scores[arm]})
        for first, second in zip(rollouts, rollouts[1:]):
            pick = lambda wanted: {key: value for key, value in scores[arm].items() if key[1] == wanted}
            d = paired(pick(first), pick(second), part_of, args.draws)["all"]
            if d:
                summary["between_rollouts"][f"{arm} {second} - {first}"] = d
                lines.append(f"{arm}, rollout {second} minus rollout {first} ({d['tasks']} tasks): {fmt(d['difference'], True)}, "
                             f"tasks better / same / worse {d['better']} / {d['same']} / {d['worse']}")
    lines += ["", "### Usage", ""]
    for arm in ARMS:
        lines.append(f"- {arm}: " + json.dumps(summary["usage"][arm], ensure_ascii=False))
    if args.leak_pattern:
        hits = leaked_runs(args.out / "notebook", args.leak_pattern)
        kept = {k: v for k, v in scores["notebook"].items() if k not in hits}
        tables = {"control": summary["scores"]["control"], "notebook": arm_table(kept, part_of, lost["notebook"])}
        summary["leak"] = {"pattern": args.leak_pattern, "runs_matching": len(hits),
                           "tasks_matching": len({slug for slug, _ in hits}),
                           "scores_without_them": tables["notebook"],
                           "paired_without_them": paired(scores["control"], kept, part_of, args.draws)}
        lines += ["", f"### Without the {len(hits)} notebook runs whose retrieved text matches `{args.leak_pattern}`", ""]
        lines += score_lines("Scores (percent)", tables, summary["leak"]["paired_without_them"])
    text = "\n".join(lines) + "\n"
    (args.out / "summary.md").write_text(text, encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
