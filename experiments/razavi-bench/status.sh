#!/usr/bin/env bash
# Where a full run stands, and its results so far. Safe while run.sh is still going: it only
# reads the run's files and writes under $RB_OUT/interim.
#   1 per arm: answers made of 150, attempts by status, and when the arm should finish
#   2 with "scores": grades the rollouts that are finished in every arm that has answers, and
#     prints the same summary as the full run (about 5 seconds per answer not yet graded)
#
#   source ~/.config/razavi-bench/env.sh
#   bash experiments/razavi-bench/status.sh            # progress only
#   bash experiments/razavi-bench/status.sh scores     # progress, then results so far
set -euo pipefail
cd "$(dirname "$0")/../.."
X=experiments/razavi-bench
for name in RB_BENCH RB_OUT; do
  [ -n "${!name:-}" ] || { echo "missing environment: $name (source env.sh first)"; exit 2; }
done
FULL="$RB_OUT/full"
INTERIM="$RB_OUT/interim"
RUNNERS="control $(pgrep -fc 'run_agent\.py.*--arm control' || true), notebook $(pgrep -fc 'run_agent\.py.*--arm notebook' || true)"
python3 - "$FULL" "$INTERIM" "$RB_BENCH" "${1:-}" "$RUNNERS" <<'PY'
import json, statistics, sys, time
from collections import Counter
from pathlib import Path

full, interim, bench, want_scores = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4] == "scores"
rows = lambda path: [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()] if path.exists() else []
tasks = len(rows(bench / "data" / "tasks.jsonl"))
total = 3 * tasks
print(f"now: {time.strftime('%F %T')} | runners answering now: {sys.argv[5]}")
finished = {}
for arm in ("control", "notebook"):
    answers, runs = rows(full / arm / "answers.jsonl"), rows(full / arm / "runs.jsonl")
    if not runs:
        print(f"{arm}: not started")
        continue
    # Two runners may have worked on the same arm: count each (task, rollout) once.
    have = {(row["task_slug"], int(row["rollout"])) for row in answers}
    per_rollout = Counter(rollout for _, rollout in have)
    last = max(per_rollout) if per_rollout else 0
    # A task whose every attempt failed gets no answer: its rollout is still finished.
    lost = {(row["task_slug"], int(row["rollout"])) for row in runs
            if not row.get("accepted") and int(row.get("attempt") or 0) >= 3} - have
    given_up = Counter(rollout for _, rollout in lost)
    finished[arm] = {r for r, n in per_rollout.items() if r < last or n + given_up[r] >= tasks}
    walls = [row["wall_s"] for row in runs if row.get("accepted") and row.get("wall_s")]
    print(f"{arm}: {len(have)} of {total} answers | per rollout {dict(sorted(per_rollout.items()))} | "
          f"attempts {dict(Counter(row['status'] for row in runs))} | tasks given up {sum(given_up.values())} | median {statistics.median(walls) if walls else 0:.0f} s per answer")
    # Where the attempts that hit the time limit were when they were stopped.
    cut = [row for row in runs if row.get("status") == "timeout"]
    if cut:
        waiting, idle = Counter(), []
        for row in cut:
            calls = row.get("timeline") or []
            if calls and calls[-1].get("s") is None:
                waiting[calls[-1]["tool"]] += 1
            else:
                idle.append((row.get("wall_s") or 0) - (calls[-1]["at"] + calls[-1]["s"] if calls else 0))
        print(f"   {len(cut)} attempts hit the time limit: {sum(waiting.values())} while a tool call had not returned {dict(waiting)}, "
              f"{len(idle)} while the model was working" + (f" (median {statistics.median(idle) / 60:.0f} min since its last tool result, "
              f"median {statistics.median([len(row.get('timeline') or []) for row in cut]):.0f} tool calls before)" if idle else ""))
    # What an attempt that answered did, against one that hit the time limit (medians per attempt).
    for label, group in (("answered", [row for row in runs if row.get("accepted")]), ("hit the limit", cut)):
        if group:
            names = sorted({name for row in group for name in row.get("tools") or {}})
            calls = {name: statistics.median([(row.get("tools") or {}).get(name, 0) for row in group]) for name in names}
            in_tools = statistics.median([sum((row.get("tool_seconds") or {}).values()) for row in group])
            wall = statistics.median([row.get("wall_s") or 0 for row in group])
            print(f"   {label} ({len(group)}): {wall / 60:.0f} min, of which {in_tools / 60:.0f} min inside tool calls | "
                  f"{statistics.median([row.get('tool_result_chars') or 0 for row in group]) / 1000:.0f}k chars returned by tools | "
                  f"saved results read {statistics.median([row.get('saved_tool_results_read') or 0 for row in group]):g} | calls "
                  + ", ".join(f"{name} {count:g}" for name, count in calls.items() if count))
    # The pace of the last 30 accepted answers, from the times their transcripts were written.
    stamps = sorted((full / arm / "transcripts" / str(row["transcript"])).stat().st_mtime
                    for row in runs if row.get("accepted") and (full / arm / "transcripts" / str(row.get("transcript"))).exists())[-30:]
    if len(have) + len(lost) >= total:
        print("   finished")
    elif len(stamps) >= 5 and time.time() - stamps[-1] < 2 * 3600:
        left = (total - len(have) - len(lost)) * (stamps[-1] - stamps[0]) / (len(stamps) - 1)
        print(f"   last answer {(time.time() - stamps[-1]) / 60:.0f} min ago | about {left / 3600:.1f} h left for this arm, "
              f"until {time.strftime('%m-%d %H:%M', time.localtime(time.time() + left))}")
    else:
        print("   no estimate: too few answers yet, or nothing written for two hours (is run.sh still running?)")
if not want_scores:
    sys.exit(0)
if not finished or not set.intersection(*finished.values()):
    print("no rollout is finished yet in every arm that has started; nothing to grade")
    sys.exit(3)
keep = set.intersection(*finished.values())
print(f"grading rollouts {sorted(keep)} of: {', '.join(finished)}")
for arm in finished:
    target = interim / arm
    target.mkdir(parents=True, exist_ok=True)
    kept = [row for row in rows(full / arm / "answers.jsonl") if int(row["rollout"]) in keep]
    (target / "answers.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept), encoding="utf-8")
    (target / "runs.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows(full / arm / "runs.jsonl")
                                               if int(row["rollout"]) in keep), encoding="utf-8")
    link = target / "transcripts"
    if not link.exists():
        link.symlink_to(full / arm / "transcripts")
PY
[ "${1:-}" = scores ] || exit 0
for arm in control notebook; do
  [ -s "$INTERIM/$arm/answers.jsonl" ] || continue
  python3 $X/judge.py --bench "$RB_BENCH" --resume --input "$INTERIM/$arm/answers.jsonl" --output "$INTERIM/$arm/scores.jsonl" | tail -3
done
python3 $X/summarize.py --bench "$RB_BENCH" --out "$INTERIM" --leak-pattern 'Analog Design Experiments With AI'
echo "===== results so far: $INTERIM/summary.md"
