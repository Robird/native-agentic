# mental-model

最小实验起点：围绕 `state_trajectory_v1 -> trajectory_evaluation_v1 -> sample_packet_v1` 的最小实验仓库，用真实 DeepSeek API 观察生成、评估与 repair routing。

README 现在主要做索引；如果你只是想跑日常 workflow，请先看 `docs/quick-start.md`。

## 1. 三个正式 daily presets

当前日常路径已经正式固定为：

- `smoke`：最小整链健康检查。
- `inspect`：repair 保持关闭，先读 packet / evaluator 判定。
- `repair`：在 `inspect` 稳定后，再开有界 repair。

三者都使用 `scripts/run_sample_pipeline.py`；差别只在日常参数组合和阅读顺序，不新增脚本，并把 `interfaces/` 留档明确降到 expert path。

teacher / evaluator 的正式 input 留档现在被明确归到 expert trace path：默认不会创建 `results/<run_id>/interfaces/`，只有显式设置 `WRITE_INTERFACES=1` 时才会写出顶层 `interfaces/` 与 repair attempt 下的接口快照文件。

日常只需要优先记住这几个 knobs：

- `RUN_ID`
- `MODEL_PROFILE`
- `SAMPLES`
- `AUTO_REPAIR`
- `MAX_REPAIR_ATTEMPTS`

更细的 stage / model / schema / stop-gate 覆写仍然保留，但现在被明确降到 expert overrides。具体分层见 `docs/quick-start.md`。

## 2. 核心入口

- `scripts/run_sample_pipeline.py`：最小生成-评估一体化 orchestrator，把单条样本落成 `sample_packet_v1`。
- `scripts/generate_state_trajectories.py`：DeepSeek 绑定的状态轨迹语料生成器，支持单阶段和“两阶段教师压缩”管线。
- `scripts/evaluate_trajectories.py`：按“工作宪法”对轨迹进行结构化评估。
- `scripts/run_experiment.py`：接口对照实验器；`scripts/run_experiment.sh` 保留为兼容入口。

核心资产与配置：

- `schemas/sample_packet_v1.json`：当前 canonical sample unit。
- `schemas/state_trajectory_v1.json`：状态轨迹样本 schema。
- `schemas/teacher_analysis_v1.json`：教师中间稿 schema。
- `schemas/teacher_agent_input_v1.json` / `schemas/evaluator_agent_input_v1.json`：正式接口 contract。
- `schemas/trajectory_evaluation_v1.json`：轨迹评估结果 schema。
- `schemas/repair_instruction_v1.json`：repair 指令 contract。
- `profiles/evaluation_profile_constitutional_v1.json`：评估 profile。
- `profiles/failure_taxonomy_v1.json`：failure taxonomy。
- `profiles/feedback_protocol_v1.json`：feedback protocol。

## 3. 结果心智模型

日常优先看的，是 daily artifacts：

- `results/<run_id>/sample_packets.jsonl`
- `results/<run_id>/sample_packets/*.json`
- `results/<run_id>/pipeline_summary.txt`
- `results/<run_id>/pipeline_manifest.json`

其中 `pipeline_manifest.json` 现在定位为稳定索引页：只保留 run 级摘要、daily knobs、关键 daily artifacts 和 stage manifest refs；更细的 generation / evaluation 元数据去看各自的 `generate/manifest.json` 与 `evaluate/manifest.json`。

如果本轮实际发生了 repair attempt，顶层还会额外物化 `repair_summary.txt` 和 `repair_summary.jsonl`；如果只是打开了 `AUTO_REPAIR=1`，但没有样本进入实际 repair 分支，manifest 和 daily path 都不会再把空 repair summary 当成默认产物。

如果你跑的是生成器或评估器单阶段脚本，对应的 daily artifacts 是：

- `results/<run_id>/trajectories.jsonl` 和 `results/<run_id>/trajectory_summary.txt`
- `results/<run_id>/evaluations.jsonl` 和 `results/<run_id>/evaluation_summary.txt`

更深的 trace artifacts 仍然保留，但不再作为 README 的主入口：

- `generate/` 和 `evaluate/`
- `interfaces/`（仅 `WRITE_INTERFACES=1` 时落盘）
- `requests/`、`raw/`
- `teacher_requests/`、`teacher_raw/`
- `evaluation_requests/`、`evaluation_raw/`
- `repair_attempts/`

## 4. 文档索引

- `docs/quick-start.md`：日常入口，正式定义 `smoke` / `inspect` / `repair`，并区分 daily knobs 与 expert overrides。
- `docs/sample-packet-and-agent-interfaces.md`：sample packet 与 teacher / evaluator 正式接口说明。
- `docs/design-simplification-audit.md`：为什么当前优先做 surface 收缩，而不是继续扩 repair / framework。
- `docs/sprints/README.md`：sprint briefs 与 subagent briefing 约定。
- `docs/roadmap.md`：阶段性路线图。
- `docs/vision.md`：当前愿景与关键发现。
- `docs/evaluation-principles.md`：评价标准的方法论框架。
- `docs/research/custom-agent-alignment-observations.md`：四个 custom agent 的研究备忘。

## 5. 仓库布局

- `data/scenarios/`：手写第三人称数据点。
- `profiles/`：评估 profile、failure taxonomy、feedback protocol。
- `schemas/`：状态轨迹、评估结果、sample packet 与 repair 指令 schema。
- `scripts/`：实验器、生成器、评估器与一体化 orchestrator。
- `results/`：每次运行落盘的 daily artifacts 与 trace artifacts。