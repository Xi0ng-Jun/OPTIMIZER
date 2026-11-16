#!/usr/bin/env bash
# What the pre-run check left open, without starting any run:
#   1 what each attempt of the smoke run did, call by call, with times
#   2 whether the notebook endpoint answers a direct connection
#   3 whether the rule that keeps the agent out of the benchmark clone holds
#   4 how long the notebook's tools take on one textbook question, and how much comes back
# Every part runs even if an earlier one fails. Nothing a tool returned is printed.
#
#   source ~/.config/razavi-bench/env.sh
#   bash experiments/razavi-bench/diagnose.sh 2>&1 | tee "$RB_OUT/diagnose.log"
set -uo pipefail
cd "$(dirname "$0")/../.."
X=experiments/razavi-bench
for name in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN SILICON_NOTEBOOK_MCP_URL SILICON_NOTEBOOK_AGENT_TOKEN RB_MODEL RB_SMALL_MODEL RB_OUT; do
  [ -n "${!name:-}" ] || { echo "missing environment: $name (source env.sh first)"; exit 2; }
done
PROBE="--model $RB_MODEL --small-model $RB_SMALL_MODEL --claude-bin ${RB_CLAUDE_BIN:-claude}"
part() { printf '\n===== part %s: %s  [%s] =====\n' "$1" "$2" "$(date '+%F %T')"; }

echo "code: $(git rev-parse --short HEAD 2>/dev/null || echo unknown) | claude: $(${RB_CLAUDE_BIN:-claude} --version 2>/dev/null | head -1)"
part 1 "what the smoke runs did"
python3 $X/timeline.py --out "$RB_OUT/smoke"
part 2 "direct connection to the notebook endpoint"
python3 $X/probe.py ping
part 3 "read isolation"
python3 $X/probe.py isolation $PROBE
part 4 "how long the notebook's tools take (up to 15 minutes per call)"
python3 $X/probe.py latency --timeout 900
printf '\n===== diagnostics finished  [%s] =====\n' "$(date '+%F %T')"
