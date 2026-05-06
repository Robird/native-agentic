# Sample Packet And Agent Interfaces

这份文档把“生成器”和“评估器”之间的隐式约定，整理成当前仓库可执行的第一版正式接口。

## 1. 当前最小原则

下一阶段的最小资产单位不再是“单独一条 trajectory JSONL 记录”，而是 `sample_packet_v1`。

原因很直接：

- 轨迹本身不是全部，还需要知道它来自哪个生成 profile、哪个 teacher 中间稿、哪个 evaluation profile。
- 一条样本被评为 `revise` 后，后续动作也应该是结构化的，而不是靠人回忆当时为什么不满意。
- 底层 DeepSeek/OpenAI 兼容 request payload 只是 transport 形式，不应该直接被当作 agent interface。

## 2. Canonical Sample Unit

`sample_packet_v1` 是当前仓库的 canonical sample unit。

它包含 12 个稳定块：

- `scenario`：场景快照，保证样本可脱离原始 scenario 文件单独理解。
- `attempt_metadata`：当前落盘的是初始尝试还是 repair 尝试，以及它是否来自自动修复。
- `pipeline`：这条样本处于哪条生成-评估管线下，使用了什么 schema/profile。
- `contracts`：teacher agent 和 evaluator agent 的正式接口声明与输入文件引用。
- `artifacts`：teacher_analysis、trajectory、evaluation 三类主要产物。
- `repair_history`：若样本经历过自动修复，记录每轮 repair 的输入动作、结果状态和 provenance 引用。
- `failure_assessment`：failure taxonomy 命中的缺陷标签、主 failure 和严重度。
- `feedback_decision`：feedback protocol 选出的建议状态、建议动作和修复阶段。
- `summary`：最常用的检索字段，例如 chosen action、verdict、axis scores。
- `quality_signals`：污染风险、过度解释风险、世界模型一致性等信号。
- `review_state`：当前状态和建议后续动作。
- `provenance`：底层 vendor request/raw response 引用、模型、usage 等追踪信息。

对应 schema：

- `schemas/sample_packet_v1.json`

## 3. Teacher Agent Interface

teacher agent 的正式接口不是 DeepSeek transport payload，而是下面这个结构化输入：

- 输入 schema：`schemas/teacher_agent_input_v1.json`
- 输出 schema：`schemas/teacher_analysis_v1.json`

teacher agent 负责：

- 读取第三人称 scenario snapshot
- 给出较充分但仍聚焦的分析教师中间稿
- 显式给出 recommended packet 和 compression guidance

teacher agent 不负责：

- 输出最终训练样本
- 做宪法式质量判定
- 直接给出 keep/revise/reject verdict

## 4. Evaluator Agent Interface

evaluator agent 的正式接口同样独立于 vendor transport：

- 输入 schema：`schemas/evaluator_agent_input_v1.json`
- 输出 schema：`schemas/trajectory_evaluation_v1.json`

evaluator agent 负责：

- 读取 scenario snapshot 与最终 trajectory
- 按 evaluation profile 做结构化打标
- 输出 verdict、rewrite priority、axis scores、failure_assessment 和 feedback_decision

evaluator agent 不负责：

- 重写 trajectory 正文
- 补写教师稿
- 直接替代生成器继续出下一条动作

## 5. Failure Taxonomy And Feedback Protocol

当前评估层已经不再只输出 `keep / revise / reject` 这种粗标签，而是多了一层可路由结构：

- failure taxonomy profile：`profiles/failure_taxonomy_v1.json`
- feedback protocol profile：`profiles/feedback_protocol_v1.json`

其中：

- `failure_assessment` 负责回答“这条样本到底哪里坏了，坏在什么层级，修复应从哪一层下手”。
- `feedback_decision` 负责回答“基于这些 failure 和总 verdict，下一步该走 approve、revise_prompt_local、regenerate_from_teacher、manual_review、reject 还是 rerun_generation”。

这两层都已经被挂接到：

- `schemas/trajectory_evaluation_v1.json`
- `schemas/sample_packet_v1.json`
- `scripts/evaluate_trajectories.py`
- `scripts/run_sample_pipeline.py`

## 6. Orchestrator Boundary

最小 orchestrator 的职责现在有五件事：

1. 调用现有生成脚本，落出 teacher/trajectory 产物。
2. 物化 teacher/evaluator 的正式输入文件。
3. 调用现有评估脚本，落出 evaluation 产物。
4. 把这些产物 join 成 `sample_packet_v1`，并用 `feedback_decision` 落最终 `review_state`。
5. 当 `AUTO_REPAIR=1` 且 next action 命中可执行分支时，生成 `repair_instruction_v1` 并执行 progress-gated 的有界 repair loop。

当前它还不负责：

- 无界自动修订 loop
- 复杂的多轮 regenerate / revise 策略搜索
- 复杂调度策略
- 数据集去重和版本裁剪

当前已落地的自动修复边界：

- `revise_prompt_local`：orchestrator 生成 `repair_instruction_v1`，并在 `teacher_compress` 管线下复用上一轮 `teacher_analysis`，只重跑压缩/轨迹层。
- `regenerate_from_teacher`：orchestrator 生成 `repair_instruction_v1`，从 teacher 阶段重新推导，再继续压缩与评估。
- 两条分支都会把 `attempt_metadata` 和 `repair_history` 写回最终 sample packet。
- orchestrator 会为每轮 repair 生成独立的 delta record，并把它写入 `repair_summary.jsonl`；stop policy 与后续分析共享同一份 delta，而不是各算各的。
- 当前默认 stop gate 是：若 repair 后已经 approved，或下一步不再可自动修；否则只有在 primary failure 变化、verdict 改善、或平均轴分提升时才继续下一轮。

## 7. Review State Mapping V1

当前 sample packet 里的 `review_state` 不再直接由 verdict 粗暴映射，而是优先采用 evaluator 给出的 `feedback_decision`。

当前实际的 protocol 语义是：

- `keep -> approved / approve`
- `revise + local failure -> needs_revision / revise_prompt_local`
- `revise + reasoning-layer failure -> needs_revision / regenerate_from_teacher`
- `manual_review -> manual_review / manual_review`
- `reject -> rejected / reject`
- `schema / structure failure -> manual_review / rerun_generation` 或手工兜底

如果 evaluator 输出缺失或结构不可靠，orchestrator 才会退回 fallback 规则。也就是说，现在闭环已经不只是表达“接下来该怎么处理这条样本”，而是能在 `AUTO_REPAIR=1` 时真正执行 progress-gated 的 `revise_prompt_local` / `regenerate_from_teacher` 自动修复。

## 8. Why This Matters

这一步的价值不是多了一个脚本，而是把仓库里的主语从“分散的实验输出目录”切到“可追踪、可评估、可回流的 sample packet”。

只有这样，后面才能继续做：

- failure taxonomy
- 多轮 auto revise / regenerate policy
- human review queue
- dataset versioning and release gate

否则生成器和评估器永远只是两个相邻脚本，而不是同一条数据管线。