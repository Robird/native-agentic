# mental-model

最小实验起点：用真实 DeepSeek API 比较同一情景下四种角色模拟接口的输出差异。

当前内容：

- `scripts/run_experiment.py`：Python 版实验器，负责构造请求、调用 API、解析响应和聚合结果。
- `scripts/run_experiment.sh`：兼容入口，转发到 Python 版实验器。
- `scripts/generate_state_trajectories.py`：DeepSeek 绑定的状态轨迹语料生成器，现已支持单阶段和“两阶段教师压缩”管线。
- `scripts/evaluate_trajectories.py`：按“工作宪法”对轨迹进行结构化评估的脚本。
- `scripts/run_sample_pipeline.py`：最小生成-评估一体化 orchestrator，把单条样本落成 `sample_packet_v1`。
- `data/scenarios/*.json`：两个手写第三人称数据点。
- `profiles/evaluation_profile_constitutional_v1.json`：第一版可扩展评估 profile。
- `profiles/failure_taxonomy_v1.json`：第一版 failure taxonomy profile。
- `profiles/feedback_protocol_v1.json`：第一版 feedback protocol profile。
- `schemas/repair_instruction_v1.json`：orchestrator 发给生成器的定向修复指令 contract。
- `schemas/sample_packet_v1.json`：样本资产单位的第一版 schema。
- `schemas/state_trajectory_v1.json`：状态轨迹语料的第一版机器可读 schema。
- `schemas/teacher_agent_input_v1.json`：teacher agent 正式输入 contract。
- `schemas/teacher_analysis_v1.json`：分析教师中间稿的第一版 schema。
- `schemas/evaluator_agent_input_v1.json`：evaluator agent 正式输入 contract。
- `schemas/trajectory_evaluation_v1.json`：轨迹评估结果的第一版 schema。
- `docs/vision.md`：当前愿景与关键发现。
- `docs/evaluation-principles.md`：评价标准的第一版方法论框架。
- `docs/sample-packet-and-agent-interfaces.md`：sample packet 与 teacher/evaluator 正式接口说明。
- `docs/research/custom-agent-alignment-observations.md`：四个 custom agent 默认三观/角色塑造的研究备忘。
- `docs/roadmap.md`：阶段性路线图。
- `results/`：每次运行的请求、原始响应和聚合结果会落到这里。

快速开始：

```bash
cd /repos/mental-model
python3 scripts/run_experiment.py

# 或保留旧入口
SAMPLES=3 ./scripts/run_experiment.sh
```

只跑一个场景和一个模式：

```bash
cd /repos/mental-model
MODES=roleplay SAMPLES=1 python3 scripts/run_experiment.py guard_unwinnable_001
```

可用环境变量：

- `DEEPSEEK_API_KEY`：必填。
- `BASE_URL`：默认 `https://api.deepseek.com`。
- `MODEL_PROFILE`：默认 `debug`。`debug -> deepseek-v4-flash`，`release -> deepseek-v4-pro`。
- `MODEL_ID`：可直接覆盖具体模型名；设置后优先级高于 `MODEL_PROFILE`。
- `SAMPLES`：每个场景、每个模式的采样次数，默认 `5`。
- `TEMPERATURE`：默认 `0.8`。
- `MODES`：空格分隔的模式列表，默认 `advice roleplay analysis story`。
- `RUN_ID`：手动指定结果目录名。

模型切换示例：

```bash
cd /repos/mental-model
MODEL_PROFILE=debug python3 scripts/run_experiment.py guard_unwinnable_001
MODEL_PROFILE=release python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

当前建议的用法分层：

- `debug`：日常内环开发、prompt 调试、schema 调试、失败复现。
- `release`：高价值教师样本生成、关键改动后的把关评测。
- `MODEL_ID=...`：只有在需要临时指定具体模型时使用。

状态轨迹生成器：

```bash
cd /repos/mental-model
SAMPLES=1 python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

当前默认会走两阶段教师管线：

1. 先生成 `teacher_analysis_v1` 中间稿。
2. 再把中间稿压缩成 `state_trajectory_v1` 最终样本。

如需回退到旧的单阶段直出模式：

```bash
cd /repos/mental-model
TRAJECTORY_PIPELINE=single_stage SAMPLES=1 python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

状态轨迹生成器也使用以下环境变量：

- `TRAJECTORY_PROFILE`：默认 `analysis_teacher_compress_v1`。
- `TRAJECTORY_PIPELINE`：默认 `teacher_compress`，可选 `single_stage`。
- `SCHEMA_FILE`：默认 `schemas/state_trajectory_v1.json`。
- `TEACHER_SCHEMA_FILE`：默认 `schemas/teacher_analysis_v1.json`。
- `MODEL_PROFILE`：同上，可用于 debug/release 风格切换。

轨迹评估脚本：

```bash
cd /repos/mental-model
MODEL_PROFILE=debug python3 scripts/evaluate_trajectories.py results/teacher-compress-smoke/trajectories.jsonl
```

轨迹评估脚本使用以下环境变量：

- `MODEL_PROFILE`：默认 `debug`。可切换 `debug/release`。
- `MODEL_ID`：可直接覆盖具体模型名。
- `EVALUATION_SCHEMA_FILE`：默认 `schemas/trajectory_evaluation_v1.json`。
- `EVALUATION_PROFILE_FILE`：默认 `profiles/evaluation_profile_constitutional_v1.json`。
- `RUN_ID`：手动指定结果目录名。

样本一体化管线：

```bash
cd /repos/mental-model
MODEL_PROFILE=debug SAMPLES=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

如需让 orchestrator 按 `feedback_decision` 自动执行一轮定向修复：

```bash
cd /repos/mental-model
MODEL_PROFILE=debug SAMPLES=1 AUTO_REPAIR=1 MAX_REPAIR_ATTEMPTS=1 python3 scripts/run_sample_pipeline.py guard_unwinnable_001
```

这个脚本会：

1. 调用生成脚本，产出 teacher analysis 和 trajectory。
2. 物化 teacher/evaluator 的正式输入 contract 文件。
3. 调用评估脚本，产出 constitutional evaluation。
4. 把这些产物 join 成 `sample_packet_v1`。
5. 当 `AUTO_REPAIR=1` 且路由命中 `revise_prompt_local` 或 `regenerate_from_teacher` 时，自动执行一轮受控重跑，并把 `attempt_metadata` / `repair_history` 写回最终 packet。

样本一体化管线额外使用以下环境变量：

- `GENERATOR_MODEL_PROFILE` / `GENERATOR_MODEL_ID`：只覆盖生成阶段模型选择。
- `EVALUATOR_MODEL_PROFILE` / `EVALUATOR_MODEL_ID`：只覆盖评估阶段模型选择。
- `GENERATOR_TEMPERATURE`：只覆盖生成阶段温度。
- `EVALUATOR_TEMPERATURE`：只覆盖评估阶段温度。
- `SAMPLE_PACKET_SCHEMA_FILE`：默认 `schemas/sample_packet_v1.json`。
- `REPAIR_INSTRUCTION_SCHEMA_FILE`：默认 `schemas/repair_instruction_v1.json`。
- `TEACHER_INPUT_SCHEMA_FILE`：默认 `schemas/teacher_agent_input_v1.json`。
- `EVALUATOR_INPUT_SCHEMA_FILE`：默认 `schemas/evaluator_agent_input_v1.json`。
- `AUTO_REPAIR`：默认 `0`。设为 `1` 时，允许 orchestrator 自动执行一轮修复重跑。
- `MAX_REPAIR_ATTEMPTS`：默认 `1`。限制每条样本最多自动修复几轮。

输出说明：

- `results/<run_id>/requests/*.json`：每次请求体，方便复现实验。
- `results/<run_id>/raw/*.json`：API 原始响应。
- `results/<run_id>/decisions.jsonl`：解析后的结构化记录。
- `results/<run_id>/summary.txt`：按场景和模式统计的动作标签分布。

状态轨迹生成器输出：

- `results/<run_id>/teacher_requests/*.json`：教师分析阶段请求体。
- `results/<run_id>/teacher_raw/*.json`：教师分析阶段原始响应。
- `results/<run_id>/teacher_analyses.jsonl`：教师分析中间稿。
- `results/<run_id>/trajectory_requests/*.json`：每次状态轨迹请求体。
- `results/<run_id>/trajectory_raw/*.json`：每次状态轨迹 API 原始响应。
- `results/<run_id>/trajectories.jsonl`：解析后的状态轨迹记录。
- `results/<run_id>/trajectory_summary.txt`：按场景统计的 chosen action 和污染风险摘要。
- `results/<run_id>/manifest.json`：本次生成的运行清单。

轨迹评估脚本输出：

- `results/<run_id>/evaluation_requests/*.json`：每次评估请求体。
- `results/<run_id>/evaluation_raw/*.json`：每次评估 API 原始响应。
- `results/<run_id>/evaluations.jsonl`：按工作宪法打标后的结构化评估结果。
- `results/<run_id>/evaluation_summary.txt`：总体 verdict、rewrite priority、feedback next action、primary failure 和各判断轴平均分摘要。
- `results/<run_id>/manifest.json`：本次评估运行清单。

样本一体化管线输出：

- `results/<run_id>/generate/`：生成阶段全部产物。
- `results/<run_id>/evaluate/`：评估阶段全部产物。
- `results/<run_id>/interfaces/teacher_inputs/*.json`：teacher agent 正式输入文件。
- `results/<run_id>/interfaces/evaluator_inputs/*.json`：evaluator agent 正式输入文件。
- `results/<run_id>/sample_packets/*.json`：逐条样本包。
- `results/<run_id>/sample_packets.jsonl`：聚合后的样本包 JSONL。
- `results/<run_id>/repair_attempts/.../attempt_XX/`：自动修复启用时，每轮 repair 的指令、重跑产物与接口文件。
- `results/<run_id>/pipeline_summary.txt`：整体 review_state / next_action / verdict / primary_failure 摘要。
- `results/<run_id>/pipeline_manifest.json`：一体化管线清单。

新的 evaluator 输出现在除了 axis_results 之外，还包含两块正式结构：

- `failure_assessment`：按 `failure_taxonomy_v1` 打上的 failure tags、主 failure 和严重度。
- `feedback_decision`：按 `feedback_protocol_v1` 生成的建议状态、建议下一步动作和修复阶段。

`sample_packet_v1` 会把这两块结构保留下来，并据此生成最终的 `review_state`。现在 packet 还会记录 `attempt_metadata` 和 `repair_history`，因此样本不再只是“好/坏”，而是已经具备了可路由、可追踪的下一步动作语义。

设计要点：

- 数据点统一用第三人称记录。
- 四种接口共用同一个结构化决策函数，降低文本解析噪音。
- 每个场景内置预置动作选项，并保留 `other` 逃逸口。
- 除动作标签外，还记录简短的内心、外在动作和理由，便于定性比较。

状态轨迹 schema v1 关注的字段：

- `visible_world`：当前可直接使用的外部事实。
- `recalled_memory`：此步真正被调取的记忆。
- `self_state`：主角自身状态与姿态。
- `relationship_frame`：当前关键实体与责任关系。
- `active_goals`：长期、中期、即时目标。
- `candidate_actions`：体现真实权衡的备选动作。
- `chosen_action`：以 action packet 形式表达的最终一步。
- `state_updates`：对 MemoryNotebook、GoalTree、SelfState、WorldModel 的稀疏更新。
- `quality_control`：assistant 污染与过度解释风险等质控信号。

两阶段教师管线 v1 的设计目的：

- 第一阶段保留较充分的角色分析与动作权衡。
- 第二阶段把教师稿压成更短、更稀疏、更接近未来训练样本的轨迹。
- `trajectory_summary.txt` 会额外输出稀疏度指标，例如 `avg_visible_world_items`、`avg_candidate_actions` 和 `avg_state_update_entries`。