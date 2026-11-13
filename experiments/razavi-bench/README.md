# Razavi-Bench：notebook 资料带来多少提升

问题：装了大量模拟电路资料的 silicon-notebook 笔记本，在 [Razavi-Bench](https://github.com/Arcadia-1/razavi-bench)（50 道模拟电路题，46 道带电路图，0–4 分）上能带来多少帮助。

做法：同一个模型、同一套提示词，用无交互的 Claude Code 答两遍，两组只差一个变量。

| | 对照组 `control` | 实验组 `notebook` |
|---|---|---|
| 输入 | 题干 + 电路图 | 相同 |
| 工具 | 读文件、写文件 | 相同，加 silicon-notebook 的 MCP 工具 |
| 提示词 | — | 多一段：先查 notebook，`ask` 只用 `output="evidence"` |

每题答 3 次。裁判沿用基准自带的 `tools/evaluate_answers.py`（提示词、评分规则、解析和重试都不变），由 `judge.py` 把其中的网络请求换成 Claude Code 所用的接口，裁判模型用内部模型，并先用基准已公开的答案校准。结果是两组的分数和按题配对的差值。

这个结果不能上榜：榜单规则是不带工具、不带资料。可以和榜单上同型号的裸答分数并列作参照（GLM 5.3 Flash：总分 79.8，Part 1 93.3，Part 2 59.6，2026-11-10 数据）。

## 文件

| 文件 | 作用 |
|---|---|
| `run_agent.py` | 跑一组：每个（题目，轮次）在只含题干和图的临时目录里调用一次 `claude -p`，收 `answer.md` |
| `probe.py` | 三项检查：图片是否送到模型、MCP 是否连通、notebook 里有没有题目出处的那两篇文章 |
| `judge.py` | 运行基准自带的裁判脚本，只把网络请求换成 Anthropic 格式，复用答题所用的地址和密钥 |
| `calibrate.py` | 内部裁判与 Razavi 本人打分、与官方裁判打分的一致程度 |
| `summarize.py` | 两组分数、配对差值和置信区间、notebook 使用情况、剔除泄题运行后的分数 |
| `mcp.notebook.json` / `mcp.empty.json` | 两组的 MCP 配置；地址和 token 用环境变量占位，文件里没有真实值 |
| `env.example.sh` | 环境变量模板 |
| `SERVER-CHECK-PROMPT.md` | 给服务器端 Claude 的检查指令 |
| `tests/` | 离线测试（假的 `claude`，不联网） |

## 准备（一次）

1. 在 silicon-notebook 的「Agent 接入」页面签发 token：新建一个 Profile；默认笔记本和白名单都只选被测的笔记本；权限只勾「读取」和「问答」；不开限时回答链接。
2. 把 `env.example.sh` 复制到仓库外（如 `~/.config/razavi-bench/env.sh`），`chmod 600`，填好，之后每次先 `source` 它。
3. 克隆基准到仓库外：`git clone https://github.com/Arcadia-1/razavi-bench "$RB_BENCH"`，记下 `git -C "$RB_BENCH" rev-parse HEAD`。校准和正式评分必须用同一个提交。

## 步骤

下面的命令都在仓库根目录执行，`X=experiments/razavi-bench`。

**检查**

```bash
RB_BENCH="$RB_BENCH" python3 -m unittest discover -s $X/tests
python3 $X/probe.py image --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL"
python3 $X/probe.py mcp   --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL"
python3 $X/probe.py leak  --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL"
```

**校准裁判**（各 150 次评分调用）

```bash
J="python3 $X/judge.py --bench $RB_BENCH --resume"
CAL="$RB_OUT/calibration"; mkdir -p "$CAL"
A1="$RB_BENCH/experiments/2026-11-09-razavi-calibration/article_answers.jsonl"
A2="$RB_BENCH/experiments/2026-11-09-question-only-rerun/answers_selfhosted_glm.jsonl"
$J --input "$A1" --output "$CAL/article.scores.jsonl"
$J --input "$A2" --output "$CAL/glm.scores.jsonl"
python3 $X/calibrate.py razavi --article-answers "$A1" --scores "$CAL/article.scores.jsonl"
python3 $X/calibrate.py judge --reference "$RB_BENCH/experiments" --scores "$CAL/glm.scores.jsonl"
```

**答题**（可中断，重跑同一条命令会跳过已有答案）

```bash
for arm in control notebook; do
  python3 $X/run_agent.py --bench "$RB_BENCH" --arm $arm --out "$RB_OUT" \
    --model "$RB_MODEL" --small-model "$RB_SMALL_MODEL" --concurrency 2
done
```

试跑时加 `--rollout 1 --task-slug <slug>`（可重复）。

**评分与汇总**

```bash
for arm in control notebook; do
  $J --input "$RB_OUT/$arm/answers.jsonl" --output "$RB_OUT/$arm/scores.jsonl"
done
python3 $X/summarize.py --bench "$RB_BENCH" --out "$RB_OUT" --leak-pattern 'Analog Design Experiments With AI'
```

## 运行规则

- 每次尝试都在一个名字中性的新目录里进行，目录里只有 `instruction.md` 和 `figure-1.png`；题目的 slug 会提示答案，不出现在提示词和目录名里。
- 内置工具只开 Read 和 Write；不联网，没有 shell。实验组另外允许 `silicon-notebook` 的全部工具，所以 token 的权限必须只有读取和问答。
- 下列情况重新采样，最多 3 次：超时、没有产出答案、实验组没连上 MCP、有图却没读图、实验组一次都没查 notebook。前三种若 3 次都如此，这一格没有答案；后两种若 3 次都如此，按最后一次的答案评分并保留状态标记。
- `runs.jsonl` 逐次记录：工具调用次数、是否读图、`ask` 有没有不带 `output="evidence"`、有没有读运行目录之外的文件。

## 数据约束

- 基准的题目、图和参考答案不得再分发，也不得并入其它评测套件（见其 `LICENSE`）：不复制进本仓库，不加入任何 notebook。可以发表汇总分数，需注明出处。
- `$RB_OUT` 下的答案和 `transcripts/` 含有从 notebook 检索出的原文，留在运行的机器上，不提交。对外只给 `summary.md` 里的汇总数字。
- 服务地址和 token 只存在于仓库外的 `env.sh`。

## 已知限制

- 分辨率：50 道题，3–4 分以内的差异不作解读；看 `summary.md` 里的置信区间。
- 裁判不是官方裁判：两组的差值可比，绝对分数与榜单只能近似比较，偏差由校准一步给出。
- `ask` 只取证据这一条靠提示词约束，MCP 无法强制；违反的次数在汇总里报告。
- MCP 的 `ask` 不能排除某篇资料。如果 notebook 里有题目出处的两篇文章，汇总会另报一份剔除了检索到它们的运行之后的分数。
- 答题用的是 Claude Code 加内部模型，不是基准 agentic 轨道的 mini-swe-agent；图片由模型自己用 Read 打开，而不是随首条消息附上。
