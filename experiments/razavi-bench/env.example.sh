# Copy to a file outside the repository (for example ~/.config/razavi-bench/env.sh), `chmod 600`
# it, fill it in, and `source` it in the shell before any command of this experiment.
# The filled-in file holds credentials and the service address: never commit it or paste it.

# The API group that serves both the answer model and the judge: the two values of the
# `claude-spec` alias.
export ANTHROPIC_BASE_URL=""
export ANTHROPIC_AUTH_TOKEN=""

# silicon-notebook Agent access: the MCP endpoint printed on the onboarding page, and the token
# issued on the "Agent 接入" page (read + ask, allowlist: the one notebook under test).
export SILICON_NOTEBOOK_MCP_URL=""
export SILICON_NOTEBOOK_AGENT_TOKEN=""

# Judge model, called through the same API group.
export RAZAVI_JUDGE_MODEL="GLM-5.3-spec"

# Answer model, and the model Claude Code uses for its own background calls.
export RB_MODEL="GLM-5.3-spec"
export RB_SMALL_MODEL="DeepSeek-V4-Flash-special"

# Where the benchmark is cloned and where run outputs go. Both outside this repository.
export RB_BENCH="$HOME/razavi-bench"
export RB_OUT="$HOME/razavi-bench-runs/run1"
