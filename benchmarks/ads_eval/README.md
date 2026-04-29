# Ads Agent 评测框架

按 FDABench 思路，对当前 agent 在广告业务域的多步推理能力做端到端评测。

## 跑一次

确保 agent 后端**没在跑**（避免 mock server 端口冲突——评测脚本自己会起 mock）：

```bash
.venv/Scripts/python.exe -m benchmarks.ads_eval.run_eval
```

只跑某条任务：

```bash
.venv/Scripts/python.exe -m benchmarks.ads_eval.run_eval --filter q001
```

输出：

- 终端汇总表（每题 pass/fail、Tool Recall、Answer Contains、Latency）
- `results.json` 详细记录（用于 CI / 趋势分析 / 失败诊断）

## 评分维度（参考 FDABench 论文）

| 指标 | 含义 | 权重 |
|---|---|---|
| **Tool Recall** | agent 是否调对了 SKILL.md 子命令 + 必备参数子串 | 1/3 |
| **Answer Contains** | 最终文本是否包含 ground-truth 关键子串 | 1/3 |
| **Numerical EM** | 数值题精确匹配（仅当 task 设了 `answer_numerical`）| 1/3 |
| Overall | 三者均分；全部 pass 才算总体 pass | - |

ROUGE-L 用于报告类暂未实现，已在 `tasks.yaml` 留字段占位。

## 加题

照葫芦画瓢往 `tasks.yaml` 末尾追加：

```yaml
- id: q010
  category: single-choice            # single-choice / multi-choice / report
  difficulty: easy                    # easy / medium / hard
  query: 你的自然语言问题
  expected:
    tools_must_call:
      - name: query-metrics           # 必须调用的子命令
        args_must_contain: ["click"]   # 命令字符串必须包含的子串
    answer_contains:
      - "关键词"                       # 文本里必须有的子串
    answer_numerical: 27266           # 可选：数值精确匹配
```

不需要写代码——只改 yaml。

## 设计取舍

- **In-process 调用 agent**：直接用 `agent.astream_events`，不走 SSE。优点是快、不用启 uvicorn、每题独立 thread_id（无状态污染）。代价是 SSE 链路本身（runner / WebSSEChannel）不被覆盖——这一层有 unit tests 已经测过。
- **串行跑**：避免 mock server 并发干扰。如果以后需要并行加速，可以加 semaphore + 给每个题独立 user_id 隔离 mock 数据。
- **默认 tool 用 stub**：`send_plan`（默认 framework tool）不做实际 IO，只把调用记录到 captures 里供打分。LLM 看到的工具签名跟生产 WebSSEChannel 完全一致——`send_plan` 由 `make_default_tools(channel)` 工厂构造，stub channel 实现不同的 `send_plan` 方法即可。

## 跟 FDABench 的差异

我们这是**广告域专用**评测：
- 题库基于部署方放进 `skills.md_dir` 的真实/mock 业务 skill 手工构造（框架不内置 skill）
- 不评估多模态（PDF/音频/视频）——业务里用不到
- 不引入 SQL 执行器 / 文件解析器等通用工具

如果将来要做"广告业务领域 benchmark 公共化"，把题库切到独立仓 + 加 license 即可。
