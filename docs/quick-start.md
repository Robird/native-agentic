# Quick Start

这份文档面向日常使用者，正式把三个 daily presets 固定为：

- `smoke`
- `inspect`
- `repair`

它们都基于同一个 `scripts/run_sample_pipeline.py`，只是日常参数组合和阅读顺序不同。这里不新增 CLI，也不修改 manifest 行为；`interfaces/` 留档则被明确降到 expert path。

`interfaces/` 现在被明确降到 expert trace path：默认不创建目录也不落 teacher/evaluator input 文件；只有显式设置 `WRITE_INTERFACES=1` 时才会写出这些接口快照。

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

## 2. 三个正式 daily presets

日常路径默认按 `smoke -> inspect -> repair` 递进。除非你已经确认 evaluator 路由稳定，否则不要跳过 `inspect` 直接开 repair。

### A. `smoke`

最小整链健康检查。目标不是读全量 trace，而是先确认生成、评估、packet 落盘和 review routing 都没坏。

```bash
cd /repos/mental-model
RUN_ID=smoke-guard MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=0 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

先看这些 daily artifacts：

- `results/smoke-guard/pipeline_summary.txt`
- `results/smoke-guard/sample_packets.jsonl`
- `results/smoke-guard/pipeline_manifest.json`

### B. `inspect`

和 `smoke` 一样先不开 repair，但它是正式的“读 packet / evaluator 判定”预设。适合调 prompt、新场景或新评估规则时先看系统到底怎么判。

```bash
cd /repos/mental-model
RUN_ID=inspect-guard MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=0 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

重点看：

- `results/inspect-guard/sample_packets/<sample_id>.json` 里的 `summary`
- `results/inspect-guard/sample_packets/<sample_id>.json` 里的 `failure_assessment`
- `results/inspect-guard/sample_packets/<sample_id>.json` 里的 `feedback_decision`
- `results/inspect-guard/sample_packets/<sample_id>.json` 里的 `review_state`
- `results/inspect-guard/pipeline_summary.txt`

先确认“错在何处、系统建议怎么修”，再决定是否打开 repair loop。

### C. `repair`

只有在 `inspect` 结果可接受、你确实想观察 repair 分支时再开。先从一轮有界 repair 开始，不要默认把 stop gate 也一起调。

```bash
cd /repos/mental-model
RUN_ID=repair-smoke MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=1 MAX_REPAIR_ATTEMPTS=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

如果本轮实际发生了 repair attempt，再看这些文件：

- `results/repair-smoke/repair_summary.txt`
- `results/repair-smoke/repair_summary.jsonl`
- `results/repair-smoke/repair_attempts/`
- `results/repair-smoke/sample_packets/<sample_id>.json` 里的 `repair_history`

如果 `AUTO_REPAIR=1` 但没有任何样本进入实际 repair attempt，顶层不会生成空的 `repair_summary.txt` 或 `repair_summary.jsonl`；这表示本轮没有发生 repair，不是写盘失败。

`STOP_ON_NO_PROGRESS` 和 `MIN_REPAIR_SCORE_DELTA` 仍然保留，但它们现在被归到第 6 节的 expert overrides。

## 3. 补充脚本：只看生成 / 只看评估

下面两个脚本仍然是有效入口，但它们属于更窄的切片工具，不是本文正式化的 daily presets。

### A. 只看生成：状态轨迹生成器

适合调 trajectory prompt、teacher/compress 行为、看生成质量但暂时不关心 evaluator。

```bash
cd /repos/mental-model
RUN_ID=generate-guard MODEL_PROFILE=debug SAMPLES=1 python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

先看这些文件：

- `results/generate-guard/trajectories.jsonl`
- `results/generate-guard/trajectory_summary.txt`
- `results/generate-guard/teacher_analyses.jsonl`

### B. 只看评估：轨迹评估器

适合你已经有轨迹文件，想单独看 evaluator 如何判定 verdict、failure 和 next action。

```bash
cd /repos/mental-model
RUN_ID=evaluate-guard MODEL_PROFILE=debug python3 scripts/evaluate_trajectories.py results/generate-guard/trajectories.jsonl
```

先看这些文件：

- `results/evaluate-guard/evaluations.jsonl`
- `results/evaluate-guard/evaluation_summary.txt`

## 4. 结果怎么看：先 daily artifacts，再 trace artifacts

### Daily artifacts

如果你只想快速判断“这次 run 值不值得继续读细节”，优先看：

- `pipeline_summary.txt`
- `sample_packets.jsonl`
- `sample_packets/<sample_id>.json`
- `pipeline_manifest.json`

其中 `pipeline_manifest.json` 现在更像稳定索引页：优先给你 run 级摘要、daily knobs、stage manifest refs 和关键 daily artifacts refs；更细的 generation / evaluation 元数据留在 `generate/manifest.json` 与 `evaluate/manifest.json`。

如果你跑的是单阶段脚本，对应的 daily artifacts 是：

- `trajectories.jsonl` 和 `trajectory_summary.txt`
- `evaluations.jsonl` 和 `evaluation_summary.txt`

### Trace artifacts

只有在 daily artifacts 不能解释问题时，再回看：

- `generate/` 和 `evaluate/`
- `interfaces/`（仅 `WRITE_INTERFACES=1` 时落盘）
- `requests/`、`raw/`
- `teacher_requests/`、`teacher_raw/`
- `evaluation_requests/`、`evaluation_raw/`
- `repair_attempts/`

这样能把“日常看结论”和“深度追踪底层原因”明显分开。

## 5. Daily knobs

日常使用者通常只需要主动记住下面这层：

- `RUN_ID`：建议总是显式设置，方便复查结果目录。
- `MODEL_PROFILE`：日常内环默认用 `debug`，高价值把关再切 `release`。
- `SAMPLES`：先从 `1` 开始，确认行为稳定后再加样本数。
- `AUTO_REPAIR`：`smoke` 和 `inspect` 默认关；`repair` 才开。
- `MAX_REPAIR_ATTEMPTS`：只在 `AUTO_REPAIR=1` 时需要，日常先从 `1` 开始。

对应到三个 daily presets：

- `smoke`：`MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=0`
- `inspect`：`MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=0`，但阅读重点切到 `sample_packet` 和 `pipeline_summary`
- `repair`：`MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=1 MAX_REPAIR_ATTEMPTS=1`

`DEEPSEEK_API_KEY` 仍然是前置条件，但它不属于本文这里说的 daily knobs。

## 6. Expert overrides

下面这些配置并没有删除，只是被降到第二层。只有在 daily knobs 不够用时，再显式触碰它们。

- stage / model 覆写：`MODEL_ID`、`GENERATOR_MODEL_PROFILE`、`GENERATOR_MODEL_ID`、`EVALUATOR_MODEL_PROFILE`、`EVALUATOR_MODEL_ID`
- stage 温度覆写：`GENERATOR_TEMPERATURE`、`EVALUATOR_TEMPERATURE`
- interface 留档开关：`WRITE_INTERFACES`。设为 `1` 时，额外写出 `results/<run_id>/interfaces/` 以及 repair attempt 目录下的 `interfaces/` 输入快照；默认不写。
- repair stop gate：`STOP_ON_NO_PROGRESS`、`MIN_REPAIR_SCORE_DELTA`
- schema / contract 覆写：`SAMPLE_PACKET_SCHEMA_FILE`、`REPAIR_INSTRUCTION_SCHEMA_FILE`、`TEACHER_INPUT_SCHEMA_FILE`、`EVALUATOR_INPUT_SCHEMA_FILE`、`SCHEMA_FILE`、`TEACHER_SCHEMA_FILE`、`EVALUATION_SCHEMA_FILE`、`EVALUATION_PROFILE_FILE`
- 生成器管线覆写：`TRAJECTORY_PROFILE`、`TRAJECTORY_PIPELINE`
- 接口对照实验相关：`MODES`、`TEMPERATURE`、`BASE_URL`，用于 `run_experiment.py`

这些 expert overrides 仍然支持，但不再和日常入口同权。

## 7. 常见情况和建议动作

### 情况 1：被路由到 `manual_review`

优先怀疑结构问题，而不是样本内容本身不好。常见原因：

- 模型没有稳定产出期望结构。
- tool json 破损。
- 某一阶段 `parse_status` 不为 `ok`。

这时先去看对应 run 下的 trace artifacts，不要急着开 repair。

### 情况 2：被路由到 `revise_prompt_local`

这通常意味着问题更像“局部表达或决策层缺口”，可以先让 orchestrator 只在本地 prompt 层重跑。

### 情况 3：被路由到 `regenerate_from_teacher`

这通常意味着问题更早，已经进入 teacher reasoning 层。此时局部修压缩层往往不够，应该从 teacher 阶段重推。

### 情况 4：repair 没继续跑

这通常不是 bug。如果目录里有 `repair_summary.txt`，先看它；如果根本没有这个文件，通常表示 `AUTO_REPAIR=1` 但没有任何样本进入实际 repair attempt。

- `approved`：修完已经够好。
- `non_repairable_route`：下一跳不是可自动执行分支。
- `max_attempts_reached`：达到上限。
- `no_progress`：primary failure、verdict、平均轴分都没改善。

## 8. 一组可直接复制的命令

### `smoke` preset

```bash
cd /repos/mental-model
RUN_ID=smoke-001 MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=0 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

### `inspect` preset

```bash
cd /repos/mental-model
RUN_ID=inspect-001 MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=0 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

### `repair` preset

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

### 接口对照实验（expert path）

```bash
cd /repos/mental-model
RUN_ID=experiment-001 MODES=roleplay SAMPLES=1 python3 scripts/run_experiment.py guard_unwinnable_001
```

## 9. 当前不必一上来就做的事

先不要急着：

- 同时开很多场景和很多样本。
- 一开始就切到 `release`。
- 每次都开 repair loop。
- 一上来就读 schema 和全部接口文档。

当前更有效的策略是：

1. 先窄跑。
2. 先看 daily artifacts。
3. 再看单条 packet。
4. 最后才看 trace artifacts。

这能显著减少无效排查。