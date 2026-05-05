# mental-model

最小实验起点：用真实 DeepSeek API 比较同一情景下四种角色模拟接口的输出差异。

当前内容：

- `scripts/run_experiment.py`：Python 版实验器，负责构造请求、调用 API、解析响应和聚合结果。
- `scripts/run_experiment.sh`：兼容入口，转发到 Python 版实验器。
- `scripts/generate_state_trajectories.py`：DeepSeek 绑定的第一版状态轨迹语料生成器。
- `data/scenarios/*.json`：两个手写第三人称数据点。
- `schemas/state_trajectory_v1.json`：状态轨迹语料的第一版机器可读 schema。
- `docs/vision.md`：当前愿景与关键发现。
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
- `MODEL_ID`：默认 `deepseek-v4-flash`。
- `SAMPLES`：每个场景、每个模式的采样次数，默认 `5`。
- `TEMPERATURE`：默认 `0.8`。
- `MODES`：空格分隔的模式列表，默认 `advice roleplay analysis story`。
- `RUN_ID`：手动指定结果目录名。

状态轨迹生成器：

```bash
cd /repos/mental-model
SAMPLES=1 python3 scripts/generate_state_trajectories.py guard_unwinnable_001
```

状态轨迹生成器也使用以下环境变量：

- `TRAJECTORY_PROFILE`：默认 `analysis_teacher_v1`。
- `SCHEMA_FILE`：默认 `schemas/state_trajectory_v1.json`。

输出说明：

- `results/<run_id>/requests/*.json`：每次请求体，方便复现实验。
- `results/<run_id>/raw/*.json`：API 原始响应。
- `results/<run_id>/decisions.jsonl`：解析后的结构化记录。
- `results/<run_id>/summary.txt`：按场景和模式统计的动作标签分布。

状态轨迹生成器输出：

- `results/<run_id>/trajectory_requests/*.json`：每次状态轨迹请求体。
- `results/<run_id>/trajectory_raw/*.json`：每次状态轨迹 API 原始响应。
- `results/<run_id>/trajectories.jsonl`：解析后的状态轨迹记录。
- `results/<run_id>/trajectory_summary.txt`：按场景统计的 chosen action 和污染风险摘要。
- `results/<run_id>/manifest.json`：本次生成的运行清单。

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