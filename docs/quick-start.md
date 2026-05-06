# Quick Start

这份文档面向日常使用者，不解释全部设计细节，只回答三个问题：

1. 先跑哪条命令。
2. 结果去哪里看。
3. 什么时候该开 repair，什么时候不该开。

如果你想看 schema、sample packet 结构或 teacher/evaluator 正式接口，再回到 `README.md` 和 `docs/sample-packet-and-agent-interfaces.md`。

## 1. 前置条件

- 已有 `python3`。
- 已配置 `DEEPSEEK_API_KEY`。
- 当前工作目录在仓库根目录。

建议先确认：

```bash
cd /repos/mental-model
echo "$DEEPSEEK_API_KEY" | wc -c
```

如果输出接近 `1`，通常说明 key 没配好。

## 2. 先记住这三个常用脚本

### A. 最常用：一体化样本管线

适合日常 smoke、看整条链路是否健康、看最终 sample packet。

```bash
cd /repos/mental-model
RUN_ID=quickstart-pipeline MODEL_PROFILE=debug SAMPLES=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

这条命令会串起来：生成 -> 评估 -> 打包 sample packet。

先看这些文件：

- `results/quickstart-pipeline/pipeline_summary.txt`
- `results/quickstart-pipeline/sample_packets/`
- `results/quickstart-pipeline/pipeline_manifest.json`

### B. 只看生成：状态轨迹生成器

适合调 trajectory prompt、teacher/compress 行为、看生成质量但暂时不关心 evaluator。

```bash
cd /repos/mental-model
RUN_ID=quickstart-generate MODEL_PROFILE=debug SAMPLES=1 python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

先看这些文件：

- `results/quickstart-generate/trajectories.jsonl`
- `results/quickstart-generate/trajectory_summary.txt`
- `results/quickstart-generate/teacher_analyses.jsonl`

### C. 只看评估：轨迹评估器

适合你已经有轨迹文件，想单独看 evaluator 如何判定 verdict、failure 和 next action。

```bash
cd /repos/mental-model
RUN_ID=quickstart-eval MODEL_PROFILE=debug python3 scripts/evaluate_trajectories.py results/quickstart-generate/trajectories.jsonl
```

先看这些文件：

- `results/quickstart-eval/evaluations.jsonl`
- `results/quickstart-eval/evaluation_summary.txt`

## 3. 日常推荐 workflow

### 工作流 1：先做最小 smoke

推荐每次改 prompt、schema、评估 profile 后，都先跑：

```bash
cd /repos/mental-model
RUN_ID=smoke-guard MODEL_PROFILE=debug SAMPLES=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

这是最小但信息密度最高的一条命令。它能同时回答：

- 生成阶段有没有坏。
- evaluator 能不能稳定产出结构化结果。
- 最终 packet 被路由到 `approve`、`revise_prompt_local`、`regenerate_from_teacher` 还是 `manual_review`。

### 工作流 2：先不开 repair，先看 evaluator 到底怎么判

在调新 prompt、新场景或新评估规则时，建议先不开自动修复：

```bash
cd /repos/mental-model
RUN_ID=inspect-first MODEL_PROFILE=debug SAMPLES=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

重点看：

- `sample_packets/*.json` 里的 `summary`
- `sample_packets/*.json` 里的 `failure_assessment`
- `sample_packets/*.json` 里的 `feedback_decision`
- `sample_packets/*.json` 里的 `review_state`

先确认“错在何处、系统建议怎么修”，再决定是否打开 repair loop。

### 工作流 3：确认路由稳定后再开 repair

当你已经接受当前 evaluator 的判断逻辑，希望观察 repair 分支是否真有增益，再开：

```bash
cd /repos/mental-model
RUN_ID=repair-smoke MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=1 MAX_REPAIR_ATTEMPTS=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

如果你在试新的 stop gate，也可以显式带上：

```bash
cd /repos/mental-model
RUN_ID=repair-progress MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=1 MAX_REPAIR_ATTEMPTS=2 STOP_ON_NO_PROGRESS=1 MIN_REPAIR_SCORE_DELTA=1.0 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

开 repair 后先看这些文件：

- `results/repair-smoke/repair_summary.txt`
- `results/repair-smoke/repair_summary.jsonl`
- `results/repair-smoke/repair_attempts/`

## 4. 怎么读结果

### 最先看 `summary.txt`

- `pipeline_summary.txt`：看整体 verdict、primary failure、next action 分布。
- `trajectory_summary.txt`：看生成侧 chosen action 和稀疏度摘要。
- `evaluation_summary.txt`：看 evaluator 的总体 verdict、failure 和轴分摘要。
- `repair_summary.txt`：看 repair action、stop reason、progress signal 的聚合结果。

如果你只想快速判断“这次 run 值不值得继续读细节”，先看 summary 文件就够了。

### 再看单条 JSON

最值得读的通常是：

- `sample_packets/<sample_id>.json`
- `evaluations.jsonl`
- `trajectories.jsonl`

在 `sample_packet` 里，日常最常看的字段是：

- `summary.overall_verdict`
- `summary.primary_failure_id`
- `summary.axis_scores`
- `review_state.status`
- `review_state.next_action`
- `failure_assessment`
- `feedback_decision`
- `attempt_metadata`
- `repair_history`

### 最后再回看原始请求和响应

只有在你怀疑是模型输出坏掉、tool json 解析失败、或提示词本身有问题时，再去看：

- `requests/`
- `raw/`
- `teacher_requests/`
- `teacher_raw/`
- `evaluation_requests/`
- `evaluation_raw/`

这样能避免一开始就陷进长上下文里。

## 5. 日常默认值建议

没有特殊需要时，先用下面这组默认心智模型：

- `MODEL_PROFILE=debug`：用于日常内环、prompt 调试、失败复现。
- `SAMPLES=1`：先做窄检查，只有在行为看起来稳定后才加样本数。
- `RUN_ID=...`：建议总是显式设置，方便复查结果目录。
- `AUTO_REPAIR=0`：先观察，再修复。
- `AUTO_REPAIR=1`：只在你明确想看 repair 路由质量时开启。

更具体一点：

- 想看“整条链路能不能跑通”，用 `run_sample_pipeline.py`。
- 想看“生成器最近是不是退化了”，用 `generate_state_trajectories.py`。
- 想看“evaluator 最近是不是判太严/太松”，用 `evaluate_trajectories.py`。
- 想看“同一情景下不同接口风格输出差别”，用 `run_experiment.py`。

## 6. 常见情况和建议动作

### 情况 1：被路由到 `manual_review`

优先怀疑结构问题，而不是样本内容本身不好。常见原因：

- 模型没有稳定产出期望结构。
- tool json 破损。
- 某一阶段 `parse_status` 不为 `ok`。

这时先去看对应 run 下的 `raw/` 和 `manifest.json`，不要急着开 repair。

### 情况 2：被路由到 `revise_prompt_local`

这通常意味着问题更像“局部表达或决策层缺口”，可以先让 orchestrator 只在本地 prompt 层重跑。

### 情况 3：被路由到 `regenerate_from_teacher`

这通常意味着问题更早，已经进入 teacher reasoning 层。此时局部修压缩层往往不够，应该从 teacher 阶段重推。

### 情况 4：repair 没继续跑

这通常不是 bug，先看 `repair_summary.txt`：

- `approved`：修完已经够好。
- `non_repairable_route`：下一跳不是可自动执行分支。
- `max_attempts_reached`：达到上限。
- `no_progress`：primary failure、verdict、平均轴分都没改善。

## 7. 一组可直接复制的命令

### 跑最小整链 smoke

```bash
cd /repos/mental-model
RUN_ID=smoke-001 MODEL_PROFILE=debug SAMPLES=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

### 跑最小 repair smoke

```bash
cd /repos/mental-model
RUN_ID=repair-001 MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=1 MAX_REPAIR_ATTEMPTS=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

### 只看生成

```bash
cd /repos/mental-model
RUN_ID=gen-001 MODEL_PROFILE=debug SAMPLES=1 python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

### 只看评估

```bash
cd /repos/mental-model
RUN_ID=eval-001 MODEL_PROFILE=debug python3 scripts/evaluate_trajectories.py results/gen-001/trajectories.jsonl
```

### 跑四接口对照实验

```bash
cd /repos/mental-model
RUN_ID=experiment-001 MODES=roleplay SAMPLES=1 python3 scripts/run_experiment.py guard_unwinnable_001
```

## 8. 当前不必一上来就做的事

先不要急着：

- 同时开很多场景和很多样本。
- 一开始就切到 `release`。
- 每次都开 repair loop。
- 一上来就读 schema 和全部接口文档。

当前更有效的策略是：

1. 先窄跑。
2. 先看 summary。
3. 再看单条 packet。
4. 最后才看原始请求和响应。

这能显著减少无效排查。