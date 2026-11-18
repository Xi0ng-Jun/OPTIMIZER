#!/usr/bin/env bash
# The full run: every task three times through both arms, graded and summarized.
# Run check.sh first. Safe to run again after an interruption: answers and gradings already
# made are kept.
#   RB_ARMS         which arms to run: "control", "notebook" or both (default "control notebook")
#   RB_CONCURRENCY  how many tasks are answered at once (default 2)
#   RB_PREFLIGHT=0  skip the one-minute probes at the start (image, read isolation, call budget)
#   RB_RUN          the run's directory under $RB_OUT (default "full"). A run that answers only
#                   the notebook arm under another name takes its control arm from "full".
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
RUN=${RB_RUN:-full}
FULL="$RB_OUT/$RUN"
ARMS=${RB_ARMS:-control notebook}
CLAUDE=${RB_CLAUDE_BIN:-claude}
mkdir -p "$FULL"
if [ "$RUN" != full ] && [ ! -e "$FULL/control" ] && [ -d "$RB_OUT/full/control" ] && [[ " $ARMS " != *" control "* ]]; then
  cp -r "$RB_OUT/full/control" "$FULL/control"
  echo "control arm: copied from $RB_OUT/full/control"
fi
echo "===== start [$(date '+%F %T')] answer model: $RB_MODEL | judge model: $RAZAVI_JUDGE_MODEL | benchmark commit: $(git -C "$RB_BENCH" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "run: $RUN | arms: $ARMS | code: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ "${RB_PREFLIGHT:-1}" != 0 ]; then
  python3 $X/probe.py image --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL" --claude-bin "$CLAUDE"
  python3 $X/probe.py isolation --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL" --claude-bin "$CLAUDE"
  if [[ " $ARMS " == *" notebook "* ]]; then
    # 3 = the hook did not refuse a call beyond the budget: go on, the prompt still states the limit
    # and runs.jsonl records every call. Any other failure stops the run.
    python3 $X/probe.py budget --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL" --claude-bin "$CLAUDE" || [ $? -eq 3 ]
  fi
fi
for arm in $ARMS; do
  python3 $X/run_agent.py --bench "$RB_BENCH" --arm $arm --out "$FULL" --model "$RB_MODEL" \
    --small-model "$RB_SMALL_MODEL" --claude-bin "$CLAUDE" --concurrency "${RB_CONCURRENCY:-2}"
done
for arm in $ARMS; do
  python3 $X/judge.py --bench "$RB_BENCH" --resume --input "$FULL/$arm/answers.jsonl" --output "$FULL/$arm/scores.jsonl"
done
python3 $X/summarize.py --bench "$RB_BENCH" --out "$FULL" --leak-pattern 'Analog Design Experiments With AI'
echo "===== finished [$(date '+%F %T')] summary: $FULL/summary.md"
