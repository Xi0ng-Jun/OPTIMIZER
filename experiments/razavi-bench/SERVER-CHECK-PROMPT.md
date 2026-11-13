# 给服务器端 Claude 的检查指令

检查可以不经过 Agent，直接在终端里运行 `check.sh`（见 README）。如果要交给服务器上的 Claude 代为执行，把下面这段话给它。前提：`env.sh` 已填好，并在启动 Claude 的 shell 里 `source` 过。

---

你要为一个评测实验做运行前检查。实验说明在 `experiments/razavi-bench/README.md`，先读它。

约束：

- 不要打印、回显或写入任何 token、密钥和服务地址。
- 不要修改 `experiments/razavi-bench/` 下的脚本，不要运行 `run.sh`。
- 基准仓库（`$RB_BENCH`）里的题目和参考答案不要复制到别处，也不要添加到任何 notebook。

在仓库根目录后台运行下面的命令并等它结束。整个过程可能需要一小时以上，不要因为命令的默认时限而中断它：

```bash
mkdir -p "$RB_OUT"
nohup bash experiments/razavi-bench/check.sh >> "$RB_OUT/check.log" 2>&1 &
```

结束后原样报告 `$RB_OUT/check.log` 的全部内容。如果某一步失败，脚本会自己停下：同样原样报告日志，不要改参数重试，也不要绕过。
