# 给服务器端 Claude 的检查指令

把下面这段话交给服务器上的 Claude。前提：仓库已切到 `feat/razavi-bench` 分支；`env.sh` 已按 `experiments/razavi-bench/env.example.sh` 填好并在启动 Claude 的 shell 里 `source` 过。

---

你要为一个评测实验做运行前检查并试跑 3 道题。实验说明在 `experiments/razavi-bench/README.md`，先读它。只做下面列出的步骤，做完就停下汇报，**不要启动 50 题的正式运行**。

约束：

- 不要打印、回显或写入任何 token、密钥和服务地址。检查环境变量是否存在时只输出"已设置 / 未设置"。
- 不要修改 `experiments/razavi-bench/` 下的脚本。某一步失败就停在那一步，原样报告命令、退出码和输出的最后 30 行，不要自己改参数绕过去。
- 基准仓库（`$RB_BENCH`）里的题目和参考答案不要复制到别处，也不要添加到任何 notebook。
- 所有命令在本仓库根目录执行，`X=experiments/razavi-bench`。

步骤：

1. 环境。逐个检查这些变量是否已设置：`ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`SILICON_NOTEBOOK_MCP_URL`、`SILICON_NOTEBOOK_AGENT_TOKEN`、`RAZAVI_JUDGE_API_URL`、`RAZAVI_JUDGE_API_KEY`、`RAZAVI_JUDGE_MODEL`、`RB_MODEL`、`RB_SMALL_MODEL`、`RB_BENCH`、`RB_OUT`。报告 `claude --version` 和 `python3 --version`。运行 `claude --help`，确认这几个选项都存在：`--tools`、`--mcp-config`、`--strict-mcp-config`、`--permission-mode`、`--allowedTools`、`--output-format`。缺任何一个就停下报告。

2. 基准。如果 `$RB_BENCH` 不存在，执行 `git clone https://github.com/Arcadia-1/razavi-bench "$RB_BENCH"`。报告 `git -C "$RB_BENCH" rev-parse HEAD` 和 `wc -l < "$RB_BENCH/data/tasks.jsonl"`（应为 50）。

3. 离线测试：`python3 -m unittest discover -s $X/tests`，应全部通过。

4. 三项探针，依次运行并报告各自的完整输出：

   ```bash
   python3 $X/probe.py image --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL"
   python3 $X/probe.py mcp   --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL"
   python3 $X/probe.py leak  --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL"
   ```

   `image` 或 `mcp` 输出 FAIL 就停下。`leak` 只需原样报告它的回答。

5. 裁判试评 3 条：

   ```bash
   J="python3 $RB_BENCH/tools/evaluate_answers.py --repo $RB_BENCH --max-tokens 16000 --timeout 600 --resume"
   CAL="$RB_OUT/calibration"; mkdir -p "$CAL"
   A1="$RB_BENCH/experiments/2026-11-09-razavi-calibration/article_answers.jsonl"
   $J --input "$A1" --output "$CAL/smoke.scores.jsonl" --limit 3
   ```

   报告 3 条的 `score_0_to_4` 和 `parse_error` 字段。有 `parse_error` 非空或分数为空就停下。

6. 校准裁判（两份各 150 次评分调用），按 README「校准裁判」一节的命令执行，报告两条 `calibrate.py` 命令的完整输出。

7. 试跑 3 道题，两组各一轮：

   ```bash
   S="--task-slug part1-001-double-length-and-width-mosfet-its-intrinsic --task-slug part1-015-malformed-cmos-inverter --task-slug part2-011-structure-provide-quadrature-outputs"
   for arm in control notebook; do
     python3 $X/run_agent.py --bench "$RB_BENCH" --arm $arm --out "$RB_OUT/smoke" \
       --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL" --rollout 1 --concurrency 1 $S
     $J --input "$RB_OUT/smoke/$arm/answers.jsonl" --output "$RB_OUT/smoke/$arm/scores.jsonl"
   done
   python3 $X/summarize.py --bench "$RB_BENCH" --out "$RB_OUT/smoke" --leak-pattern 'Analog Design Experiments With AI'
   ```

   报告两次 `run_agent.py` 的输出和 `summarize.py` 的输出。

汇报格式：一张表，每步一行（步骤、通过 / 失败、关键数字），后面附各步要求的输出。另外单独回答三个问题：

- 试跑中每次运行的耗时中位数是多少（`summary` 里的 `median_wall_s`）；
- 实验组的 3 次运行各调用了哪些 notebook 工具、各几次（`runs.jsonl` 的 `tools` 字段）；
- 有没有任何一次运行的 `ask_not_evidence` 大于 0 或 `reads_outside_workdir` 非空。
