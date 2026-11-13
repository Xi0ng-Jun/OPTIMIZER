#!/usr/bin/env bash
# Every check to pass before the full run, in order, stopping at the first failure:
#   1 image probe   2 notebook connection   3 source-article leak   4 judge on 3 answers
#   5 judge calibration (300 gradings)      6 three tasks through both arms, graded and summarized
# Safe to run again: gradings and answers already made are kept. RB_FROM=<n> starts at step n.
#
#   source ~/.config/razavi-bench/env.sh
#   bash experiments/razavi-bench/check.sh 2>&1 | tee -a "$RB_OUT/check.log"
set -euo pipefail
cd "$(dirname "$0")/../.."
X=experiments/razavi-bench
for name in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN SILICON_NOTEBOOK_MCP_URL SILICON_NOTEBOOK_AGENT_TOKEN \
            RAZAVI_JUDGE_MODEL RB_MODEL RB_SMALL_MODEL RB_BENCH RB_OUT; do
  [ -n "${!name:-}" ] || { echo "missing environment: $name (source env.sh first)"; exit 2; }
done
FROM=${RB_FROM:-1}
CLAUDE=${RB_CLAUDE_BIN:-claude}
PROBE="--model $RB_MODEL --small-model $RB_SMALL_MODEL --claude-bin $CLAUDE"
JUDGE="python3 $X/judge.py --bench $RB_BENCH --resume"
CAL="$RB_OUT/calibration"; SMOKE="$RB_OUT/smoke"
A1="$RB_BENCH/experiments/2026-11-09-razavi-calibration/article_answers.jsonl"
A2="$RB_BENCH/experiments/2026-11-09-question-only-rerun/answers_selfhosted_glm.jsonl"
mkdir -p "$CAL"
step() { [ "$1" -ge "$FROM" ] || return 1; printf '\n===== step %s: %s  [%s] =====\n' "$1" "$2" "$(date '+%F %T')"; }

echo "answer model: $RB_MODEL | judge model: $RAZAVI_JUDGE_MODEL | benchmark commit: $(git -C "$RB_BENCH" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if step 1 "image probe"; then python3 $X/probe.py image $PROBE; fi
if step 2 "notebook connection"; then python3 $X/probe.py mcp $PROBE; fi
if step 3 "source-article leak"; then python3 $X/probe.py leak $PROBE; fi
if step 4 "judge on 3 answers"; then
  $JUDGE --input "$A1" --output "$CAL/smoke.scores.jsonl" --limit 3
  python3 - "$CAL/smoke.scores.jsonl" <<'PY'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
for row in rows:
    print(row["task_slug"], "| score:", row["score_0_to_4"], "| error:", row["parse_error"] or "none")
sys.exit(0 if len(rows) == 3 and all(row["score_0_to_4"] is not None for row in rows) else "FAIL: the judge did not score all 3 answers")
PY
fi
if step 5 "judge calibration"; then
  $JUDGE --input "$A1" --output "$CAL/article.scores.jsonl"
  $JUDGE --input "$A2" --output "$CAL/glm.scores.jsonl"
  echo; echo "--- against Razavi's own scores"
  python3 $X/calibrate.py razavi --article-answers "$A1" --scores "$CAL/article.scores.jsonl"
  echo; echo "--- against the official judge, on GLM 5.3 Flash's published answers"
  python3 $X/calibrate.py judge --reference "$RB_BENCH/experiments" --scores "$CAL/glm.scores.jsonl"
fi
if step 6 "three tasks through both arms"; then
  for arm in control notebook; do
    python3 $X/run_agent.py --bench "$RB_BENCH" --arm $arm --out "$SMOKE" --model "$RB_MODEL" \
      --small-model "$RB_SMALL_MODEL" --claude-bin "$CLAUDE" --rollout 1 --concurrency 1 \
      --task-slug part1-001-double-length-and-width-mosfet-its-intrinsic \
      --task-slug part1-015-malformed-cmos-inverter \
      --task-slug part2-011-structure-provide-quadrature-outputs
    $JUDGE --input "$SMOKE/$arm/answers.jsonl" --output "$SMOKE/$arm/scores.jsonl"
  done
  python3 $X/summarize.py --bench "$RB_BENCH" --out "$SMOKE" --leak-pattern 'Analog Design Experiments With AI'
  echo "--- per run (notebook arm): tools, ask without evidence, reads outside the run directory"
  python3 - "$SMOKE/notebook/runs.jsonl" <<'PY'
import json, sys
for row in map(json.loads, open(sys.argv[1])):
    print(row["task_slug"][:9], "attempt", row["attempt"], row["status"], "|", row["tools"], "|",
          row["ask_not_evidence"], "|", len(row["reads_outside_workdir"]), "|", row["wall_s"], "s")
PY
fi
printf '\n===== all requested steps finished  [%s] =====\n' "$(date '+%F %T')"
