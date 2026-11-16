#!/usr/bin/env python3
"""How far a substitute judge is from the benchmark's own grading.

The leaderboard is graded by one fixed judge model. When another model grades instead, these two
comparisons, both on answers the benchmark already published, say what its scores mean:

    calibrate.py razavi --article-answers <razavi-bench>/experiments/2026-11-09-razavi-calibration/article_answers.jsonl \
                        --scores <our scores of those answers>
        Razavi's own 0-4 scores of the 50 answers printed in his articles. Each answer is listed
        three times; the judge's score of a question is the median of its three gradings.

    calibrate.py judge --reference <razavi-bench>/experiments --scores <our scores of published answers>
        The official judge's scores of one model's published answers, answer by answer. The
        published score files hold several gradings of several versions of the answers, so an
        official score is used only when it is of the same answer text and was given against the
        reference answer that this judge also graded against. A directory stands for every score
        file beneath it.

Score files are the output of the benchmark's tools/evaluate_answers.py.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def rows(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").split("\n") if line.strip()]


def percent(scores: list[float]) -> float:
    return 100 * sum(scores) / (4 * len(scores)) if scores else float("nan")


def part_of(slug: str) -> str:
    return slug.split("-", 1)[0]


def by_part(pairs: dict[str, tuple[float, float]]) -> None:
    print(f"{'':10}{'reference':>10}{'this judge':>12}{'n':>6}")
    for part in ("part1", "part2", "all"):
        chosen = [pair for slug, pair in pairs.items() if part == "all" or part_of(slug) == part]
        print(f"{part:10}{percent([a for a, _ in chosen]):>9.1f}%{percent([b for _, b in chosen]):>11.1f}%{len(chosen):>6}")


def razavi(args: argparse.Namespace) -> None:
    truth = {row["task_slug"]: row["razavi_score"] for row in rows(args.article_answers)}
    graded: dict[str, list[int]] = {}
    for row in rows(args.scores):
        if row.get("score_0_to_4") is not None and row["task_slug"] in truth:
            graded.setdefault(row["task_slug"], []).append(row["score_0_to_4"])
    pairs = {slug: (truth[slug], statistics.median(scores)) for slug, scores in graded.items()}
    print(f"questions graded: {len(pairs)} of {len(truth)}")
    print(f"same score as Razavi: {sum(a == b for a, b in pairs.values())} / {len(pairs)}"
          "   (the official judge: 48 / 50)")
    print(f"within 1 point:       {sum(abs(a - b) <= 1 for a, b in pairs.values())} / {len(pairs)}"
          "   (the official judge: 49 / 50)")
    by_part(pairs)
    apart = sorted((slug, a, b) for slug, (a, b) in pairs.items() if a != b)
    if apart:
        print("\nquestions scored differently (Razavi, this judge):")
        for slug, a, b in apart:
            print(f"  {slug}: {a}, {b:g}")


def score_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for path in map(Path, paths):
        files += sorted(f for f in path.rglob("*.jsonl") if "score" in f.name) if path.is_dir() else [path]
    return files


def judge(args: argparse.Namespace) -> None:
    key = lambda row: (row["task_slug"], row.get("answer_sha256"))
    official: dict[tuple, dict[str, int]] = {}  # answer -> reference answer it was graded against -> score
    for path in score_files(args.reference):
        for row in rows(str(path)):
            if isinstance(row, dict) and row.get("score_0_to_4") is not None and row.get("answer_sha256"):
                official.setdefault(key(row), {})[row.get("golden_solution_sha256")] = row["score_0_to_4"]
    pairs, skipped = {}, Counter()
    for number, row in enumerate(rows(args.scores)):
        graded = official.get(key(row))
        if row.get("score_0_to_4") is None:
            skipped["this judge returned no score"] += 1
        elif graded is None:
            skipped["no official score of this answer text"] += 1
        elif row.get("golden_solution_sha256") not in graded:
            skipped["official score was given against another version of the reference answer"] += 1
        else:
            pairs[(row["task_slug"], number)] = (graded[row["golden_solution_sha256"]], row["score_0_to_4"])
    print(f"answers compared: {len(pairs)}" + "".join(f"; {n} skipped ({why})" for why, n in skipped.items()))
    if not pairs:
        return
    diffs = [b - a for a, b in pairs.values()]
    print(f"same score:     {sum(d == 0 for d in diffs)} / {len(diffs)}")
    print(f"within 1 point: {sum(abs(d) <= 1 for d in diffs)} / {len(diffs)}")
    print(f"this judge is higher on {sum(d > 0 for d in diffs)} answers and lower on {sum(d < 0 for d in diffs)};"
          f" mean difference {100 * statistics.mean(diffs) / 4:+.1f} points of 100")
    by_part({f"{slug}|{number}": pair for (slug, number), pair in pairs.items()})
    table = Counter(pairs.values())
    print("\nreference score (rows) against this judge's score (columns):")
    print("     " + "".join(f"{b:>5}" for b in range(5)))
    for a in range(5):
        print(f"{a:>5}" + "".join(f"{table.get((a, b), 0):>5}" for b in range(5)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)
    one = modes.add_parser("razavi")
    one.add_argument("--article-answers", required=True)
    one.add_argument("--scores", required=True)
    two = modes.add_parser("judge")
    two.add_argument("--reference", required=True, nargs="+")
    two.add_argument("--scores", required=True)
    args = parser.parse_args()
    {"razavi": razavi, "judge": judge}[args.mode](args)


if __name__ == "__main__":
    main()
