# Razavi-Bench：notebook 资料带来多少提升

问题：装了大量模拟电路资料的 silicon-notebook 笔记本，在 [Razavi-Bench](https://github.com/Arcadia-1/razavi-bench)（50 道模拟电路题，46 道带电路图，0–4 分）上能带来多少帮助。

做法：同一个模型、同一套提示词，用无交互的 Claude Code 答两遍，两组只差一个变量。

| | 对照组 `control` | 实验组 `notebook` |
|---|---|---|
| 输入 | 题干 + 电路图 | 相同 |
| 工具 | 读文件、写文件 | 相同，加 silicon-notebook 的 MCP 工具 |
| 提示词 | — | 多一段：先查 notebook，`ask` 只用 `output="evidence"` |

每题答 3 次。裁判沿用基准自带的 `tools/evaluate_answers.py`（提示词、评分规则、解析和重试都不变），由 `judge.py` 把其中的网络请求换成 Claude Code 所用的接口，裁判模型用内部模型，并先用基准已公开的答案校准。结果是两组的分数和按题配对的差值。

这个结果不能上榜：榜单规则是不带工具、不带资料。可以和榜单上同型号的裸答分数并列作参照（2026-11-10 数据，总分 / Part 1 / Part 2：DeepSeek V4.1 Flash 1017 为 77.3 / 83.9 / 67.5，0910 版为 69.7 / 74.4 / 62.5；GLM 5.3 Flash 为 79.8 / 93.3 / 59.6）。内部部署对应哪个版本需要另行确认。

## 文件

| 文件 | 作用 |
|---|---|
| `run_agent.py` | 跑一组：每个（题目，轮次）在只含题干和图的临时目录里调用一次 `claude -p`，收 `answer.md` |
| `probe.py` | 经过 `claude` 的检查：图片是否送到模型、MCP 是否连通、notebook 里有没有题目出处的两篇文章、读隔离规则是否生效；不经过 `claude`、直连 notebook 的检查：是否应答（`ping`）、各工具耗时和返回量（`latency`） |
| `mcp_client.py` | 直连 notebook MCP 接口的最小客户端（标准库） |
| `timeline.py` | 逐次列出一轮运行里每个工具调用的时刻、耗时、返回量和报错，不打印返回内容 |
| `judge.py` | 运行基准自带的裁判脚本，只把网络请求换成 Anthropic 格式，复用答题所用的地址和密钥 |
| `calibrate.py` | 内部裁判与 Razavi 本人打分、与官方裁判打分的一致程度 |
| `check.sh` / `run.sh` | 运行前的六步检查；正式运行（答题、评分、汇总） |
| `diagnose.sh` | 不启动任何运行的诊断：试跑的逐次时间线、直连检查、读隔离、工具耗时 |
| `summarize.py` | 两组分数、配对差值和置信区间、notebook 使用情况、剔除泄题运行后的分数 |
| `mcp.notebook.json` / `mcp.empty.json` | 两组的 MCP 配置；地址和 token 用环境变量占位，文件里没有真实值 |
| `env.example.sh` | 环境变量模板 |
| `SERVER-CHECK-PROMPT.md` | 给服务器端 Claude 的检查指令 |
| `tests/` | 离线测试（假的 `claude`，不联网） |

## 准备（一次）

1. 在 silicon-notebook 的「Agent 接入」页面签发 token：新建一个 Profile；默认笔记本和白名单都只选被测的笔记本；权限只勾「读取」和「问答」；不开限时回答链接。
2. 把 `env.example.sh` 复制到仓库外（如 `~/.config/razavi-bench/env.sh`），`chmod 600`，填好，之后每次先 `source` 它。答题模型和裁判模型可以在不同的 API 组：`ANTHROPIC_*` 是答题模型所在的组，`RAZAVI_JUDGE_BASE_URL` 和 `RAZAVI_JUDGE_AUTH_TOKEN` 是裁判所在的组。
3. 克隆基准到仓库外：`git clone https://github.com/Arcadia-1/razavi-bench "$RB_BENCH"`。校准和正式评分必须用同一个提交。

评测服务器上的实测情况（2026-11-13）：`GLM-5.3-spec` 只收文字，只能当裁判，且只在 spec 组；`DeepSeek-V4.1-Flash-special` 在 mine 组能读图，用作答题模型。

## 步骤

都在仓库根目录、普通终端里执行，先 `source` 环境变量文件。

**检查**：六步依次进行，任何一步失败就停。

```bash
mkdir -p "$RB_OUT"
bash experiments/razavi-bench/check.sh 2>&1 | tee -a "$RB_OUT/check.log"
```

| 步 | 内容 | 通过的标志 |
|---|---|---|
| 1 | 图片是否送到答题模型 | `PASS` |
| 2 | notebook 的 MCP 是否连通 | `PASS` |
| 3 | notebook 里有没有题目出处的两篇文章 | 看它的回答 |
| 4 | 裁判试评 3 条 | 三条都有分数 |
| 5 | 校准裁判（300 次评分） | 两张对比表 |
| 6 | 3 道题两组各跑一轮，评分并汇总 | 汇总表和每次运行的工具调用 |

失败后修好了再跑同一条命令，已完成的评分和答案会保留。`RB_FROM=5 bash ...` 从第 5 步开始。

**正式运行**：50 题 × 3 轮 × 2 组，然后评分、汇总。可中断，重跑同一条命令会接着做。

```bash
nohup bash experiments/razavi-bench/run.sh >> "$RB_OUT/run.log" 2>&1 &
tail -f "$RB_OUT/run.log"
```

并发用 `RB_CONCURRENCY` 调（默认 2）。结果在 `$RB_OUT/full/summary.md`。`RB_RUN=<名字>` 把这次运行放到 `$RB_OUT/<名字>/`；只跑实验组时，对照组从 `full` 复制过来。运行中用 `status.sh` 看进度，`status.sh scores` 对两组都已完成的轮次先评分汇总。`RB_ARMS=control`（或 `notebook`）只跑一组；两组互不依赖，可以分开跑，汇总时缺的一组显示为空。脚本开头会先做图片和读隔离两项探针，不通过就不开始。

**诊断**：某一步结果异常时用，几分钟到半小时，不启动任何运行。

```bash
bash experiments/razavi-bench/diagnose.sh 2>&1 | tee "$RB_OUT/diagnose.log"
```

各脚本也可以单独调用，参数见各自的 `--help`。

## 运行规则

- 每次尝试都在一个名字中性的新目录里进行，目录里只有 `instruction.md` 和 `figure-1.png`；题目的 slug 会提示答案，不出现在提示词和目录名里。
- 内置工具只开 Read 和 Write；不联网，没有 shell。实验组另外允许 `silicon-notebook` 的全部工具，所以 token 的权限必须只有读取和问答。
- 一条权限规则禁止 agent 读取基准克隆、输出目录和环境变量文件所在的目录（`probe.py isolation` 验证规则生效）。
- 实验组每次尝试之前，脚本先直连 notebook 的 MCP 接口确认它应答；不应答就每分钟重试，最长等一小时，仍不通则这次尝试记为 `notebook_down`，不调用模型。
- 实验组每次运行最多调用 notebook 10 次，其中 `ask` 最多 2 次（`run_agent.py --notebook-calls / --ask-calls`，0 为不限）。提示词里写明这个上限，超出的调用由一个 PreToolUse hook（`budget_hook.py`）拒绝，`probe.py budget` 验证 hook 生效。加这个上限的原因：2026-11-17 不设上限的第 1 轮里，101 次尝试有 53 次跑满 60 分钟，超时的运行调用工具的中位数是 24 次，答完的是 7 次 notebook 调用。
- 下列情况重新采样，最多 3 次：超时、没有产出答案、实验组没连上 MCP、有图却没读图、实验组一次都没查 notebook。前三种若 3 次都如此，这一格没有答案；后两种若 3 次都如此，按最后一次的答案评分并保留状态标记。
- 单条回复的输出上限由环境变量 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` 决定；不设时 Claude Code 对不认识的模型名用 32000。回复写到上限会被截断，这次尝试以 `no_answer` 结束并重新采样；`status.sh` 会列出这类尝试的次数，`runs.jsonl` 的 `output_limit` 记下每次尝试用的值（空表示默认）。基准自己的做法是把上限设为模型的最大输出（DeepSeek V4.1 Flash 为 943,718）。观察（2026-11-18）：内部部署 206 次尝试没有一次被截断；外部部署的对照组在默认上限下 149 次尝试有 15 次被截断；榜单上同型号 150 个回答里 45 个超过 32000 token，最长 63,663。
- `runs.jsonl` 逐次记录：工具调用次数、每次调用的时刻和耗时、是否读图、`ask` 有没有不带 `output="evidence"`、有没有读运行目录之外的文件、启动时 MCP 的连接状态。

## 数据约束

- 基准的题目、图和参考答案不得再分发，也不得并入其它评测套件（见其 `LICENSE`）：不复制进本仓库，不加入任何 notebook。可以发表汇总分数，需注明出处。
- `$RB_OUT` 下的答案和 `transcripts/` 含有从 notebook 检索出的原文，留在运行的机器上，不提交。对外只给 `summary.md` 里的汇总数字。
- 服务地址和 token 只存在于仓库外的 `env.sh`。

## 已知限制

- 分辨率：50 道题，3–4 分以内的差异不作解读；看 `summary.md` 里的置信区间。
- 三次尝试都没有答案的格子不参与各项分数和配对差值；基准的榜单把这样的格子记 0 分，汇总表最后一列按这个口径给出总分。
- 裁判不是官方裁判：两组的差值可比，绝对分数与榜单只能近似比较，偏差由校准一步给出。
- `ask` 只取证据这一条靠提示词约束，MCP 无法强制；违反的次数在汇总里报告。
- MCP 的 `ask` 不能排除某篇资料。如果 notebook 里有题目出处的两篇文章，汇总会另报一份剔除了检索到它们的运行之后的分数。
- 答题用的是 Claude Code 加内部模型，不是基准 agentic 轨道的 mini-swe-agent；图片由模型自己用 Read 打开，而不是随首条消息附上。
