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

它包含 8 个稳定块：

- `scenario`：场景快照，保证样本可脱离原始 scenario 文件单独理解。
- `pipeline`：这条样本处于哪条生成-评估管线下，使用了什么 schema/profile。
- `contracts`：teacher agent 和 evaluator agent 的正式接口声明与输入文件引用。
- `artifacts`：teacher_analysis、trajectory、evaluation 三类主要产物。
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
- 输出 verdict、rewrite priority、axis scores 和修改方向

evaluator agent 不负责：

- 重写 trajectory 正文
- 补写教师稿
- 直接替代生成器继续出下一条动作

## 5. Orchestrator Boundary

最小 orchestrator 的职责只有四件事：

1. 调用现有生成脚本，落出 teacher/trajectory 产物。
2. 物化 teacher/evaluator 的正式输入文件。
3. 调用现有评估脚本，落出 evaluation 产物。
4. 把这些产物 join 成 `sample_packet_v1`。

当前它还不负责：

- 自动修订 trajectory
- 多轮 regenerate loop
- 复杂调度策略
- 数据集去重和版本裁剪

## 6. Review State Mapping V1

当前 sample packet 里的 `review_state` 使用以下最小映射：

- `keep -> approved / approve`
- `revise -> needs_revision / regenerate_from_teacher`（有 teacher 稿时）
- `revise -> needs_revision / revise_prompt_local`（无 teacher 稿时）
- `manual_review -> manual_review / manual_review`
- `reject -> rejected / reject`
- `trajectory parse failed -> needs_revision 或 manual_review`
- `evaluation parse failed -> manual_review / manual_review`

这意味着当前闭环已经能表达“接下来该怎么处理这条样本”，但还没有自动执行这些动作。

## 7. Why This Matters

这一步的价值不是多了一个脚本，而是把仓库里的主语从“分散的实验输出目录”切到“可追踪、可评估、可回流的 sample packet”。

只有这样，后面才能继续做：

- failure taxonomy
- auto revise / regenerate policy
- human review queue
- dataset versioning and release gate

否则生成器和评估器永远只是两个相邻脚本，而不是同一条数据管线。