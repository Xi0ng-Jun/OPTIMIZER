#!/usr/bin/env bash
# The full run: every task three times through both arms, graded and summarized.
# Run check.sh first. Safe to run again after an interruption: answers and gradings already
# made are kept. RB_CONCURRENCY sets how many tasks are answered at once (default 2).
#
#   source ~/.config/razavi-bench/env.sh
#   nohup bash experiments/razavi-bench/run.sh >> "$RB_OUT/run.log" 2>&1 &
#   tail -f "$RB_OUT/run.log"
set -euo pipefail
cd "$(dirname "$0")/../.."
X=experiments/razavi-bench
for name in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN SILICON_NOTEBOOK_MCP_URL SILICON_NOTEBOOK_AGENT_TOKEN \
            RAZAVI_JUDGE_MODEL RB_MODEL RB_SMALL_MODEL RB_BENCH RB_OUT; do
  [ -n "${!name:-}" ] || { echo "missing environment: $name (source env.sh first)"; exit 2; }
done
FULL="$RB_OUT/full"
echo "===== start [$(date '+%F %T')] answer model: $RB_MODEL | judge model: $RAZAVI_JUDGE_MODEL | benchmark commit: $(git -C "$RB_BENCH" rev-parse --short HEAD 2>/dev/null || echo unknown)"
for arm in control notebook; do
  python3 $X/run_agent.py --bench "$RB_BENCH" --arm $arm --out "$FULL" --model "$RB_MODEL" \
    --small-model "$RB_SMALL_MODEL" --claude-bin "${RB_CLAUDE_BIN:-claude}" --concurrency "${RB_CONCURRENCY:-2}"
done
for arm in control notebook; do
  python3 $X/judge.py --bench "$RB_BENCH" --resume --input "$FULL/$arm/answers.jsonl" --output "$FULL/$arm/scores.jsonl"
done
python3 $X/summarize.py --bench "$RB_BENCH" --out "$FULL" --leak-pattern 'Analog Design Experiments With AI'
echo "===== finished [$(date '+%F %T')] summary: $FULL/summary.md"
