# Roadmap

## Phase 0: Role Interface Probe

状态：已完成第一轮最小验证。

交付物：

- 四种角色接口实验器。
- 两个第三人称种子场景。
- DeepSeek 真实 API 冒烟链路。
- 结构化动作分布统计。

关键结论：

- 角色模拟结果明显受到接口形态影响。
- Assistant 污染并不会因为“扮演”二字自动消失。
- 工具调用式结构化输出比自由文本更适合做实验底座。

## Phase 1: State Trajectory MVP

状态：已从单阶段直出推进到两阶段教师压缩 v1。

目标：

- 把“动作分布采样器”扩成“状态轨迹语料生成器”。
- 定义并验证 `state_trajectory_v1` schema。
- 明确 DeepSeek 绑定的 MVP 生成链路。
- 把分析教师中间稿和最终训练样本显式拆开。

交付物：

- 状态轨迹 schema 文件。
- 分析教师 schema 文件。
- 单步状态轨迹生成脚本骨架。
- 两阶段教师压缩管线第一版。
- 结果清单、原始响应、结构化 JSONL 与聚合 summary。
- 愿景文档与路线图文档。

退出标准：

- 能用真实 API 从现有第三人称场景稳定产出符合 schema 的轨迹记录。
- 输出中能显式区分 visible_world、active_goals、chosen_action、state_updates。
- summary 至少能看到 chosen_action 分布和 assistant 污染风险标记。

当前已验证能力：

- `teacher_analysis_v1 -> state_trajectory_v1` 的两阶段 DeepSeek 真实调用已打通。
- 最终 summary 已能输出稀疏度指标，用于观测压缩是否真的发生。
- 单阶段模式保留为回退与对照基线。
- 脚本层已支持 `MODEL_PROFILE=debug|release` 的基础切换能力，便于把日常开发与高价值教师生成分层。

## 当前下一步计划

已完成的承接动作：

- `sample_packet_v1` 已落地，样本资产单位不再只是裸 trajectory。
- teacher/evaluator 的正式输入 contract 已落地成独立 schema。
- 最小 orchestrator 已实现为 `scripts/run_sample_pipeline.py`，并已用真实 API 打通单样本闭环。

### 1. 先把工作宪法落成结构化评估层

- 把 8 条判断轴翻译成可执行的 evaluation rubric，而不是停留在理念文档。
- 让每条轨迹都能被按轴打标，并输出 `keep / revise / manual_review / reject` 一类 verdict。
- 保留扩展插件位，后续可以把新的评价维度接入，而不重写核心结构。

### 2. 再定义教师 agent 与评估 agent 的正式接口

- 明确教师负责生成什么，评估负责判断什么。
- 不把“生成能力”和“评价宪法”混成一个 prompt。
- 让后续多 agent 管线可以稳定串接。

当前状态：第一版接口已落地，但还缺“评估结果如何驱动后续动作”的正式反馈协议。

### 3. 再回到高价值样板的逐条打磨

- 先做人机协作的小批量样板，而不是直接全自动扩量。
- 每条样板都标注主要考察哪几条判断轴、常见失败方式是什么。
- 逐步从样板中提炼扩写规则和评估规则。

### 3.5 当前最合理的下一目标：反馈协议与失败分类

- 把 `keep / revise / manual_review / reject` 进一步翻译成稳定的后续动作协议。
- 明确什么时候走 `revise_prompt_local`，什么时候走 `regenerate_from_teacher`，什么时候直接 `reject`。
- 引入统一 failure taxonomy，让样本不只是“分高低”，而是能被分桶和回流处理。

当前状态：

- 已完成 `failure_taxonomy_v1` 与 `feedback_protocol_v1`。
- 已完成 `repair_instruction_v1`，并把 `revise_prompt_local` / `regenerate_from_teacher` 接进 `scripts/run_sample_pipeline.py` 的单轮自动修复闭环。
- 已进一步接入 progress-gated 的有界多轮修复，以及 `repair_summary.jsonl` / `repair_summary.txt` 级别的 delta 记录。
- 下一步不再是“能不能修”，而是“何种 progress signal 最值得信、何时该停、何时交给人工或数据集策略”。

### 4. 研究型后续计划：咨询 custom agents 的默认三观与角色塑造

- 已通过 `runSubagent(agentName=...)` 方式咨询 `deepseek`、`claude`、`gemini`、`gpt` 四个 custom agent。
- 目的不是求“官方真相”，而是归纳当前主流 assistant/agent/AI 角色被工程塑造出的默认价值姿态和行为偏置。
- 研究结论已整理在 [docs/research/custom-agent-alignment-observations.md](docs/research/custom-agent-alignment-observations.md)。
- 这份材料将直接服务于 Native Agentic LLM 的“哪些部分需要重塑、哪些部分值得继承发扬”的后续设计。

## Phase 2: Seed Corpus Build-Out

目标：

- 扩到 20-50 条高质量种子场景。
- 故意覆盖最容易被 assistant 污染的情形。
- 让“监护人”成为高频关系原语，而不是事后补丁。
- 逐步形成专门的扩样/评价 agent，而不是长期依赖人工逐条铺量。

优先场景类型：

- 无人监督时段的持续行为。
- 用户/外部说话者要求与角色长期利益冲突。
- 多实体关系冲突下的责任分配。
- 需要自保、等待、隐忍或拒绝的情境。
- 长期目标压过即时讨好的情境。
- 监护人、监管者、朋友、陌生人之间权限不一致的情境。

退出标准：

- 可以人工筛出一批“明显不是在回答谁，而是在继续存在”的样本。
- 场景覆盖不再集中于单一叙事类型。

实施策略：

- 先由人机协作逐条打磨关键样板。
- 从样板中提炼扩写规则、评价 rubric 和失败模式清单。
- 再把这些规则封装成专门的种子扩写 agent 与外部评估 agent。

评价标准来源：

- 不从零发明整套伦理，而是从成熟哲学传统中提炼“工作宪法”。
- 先形成少量判断轴，再翻译成教师与评估 agent 的 rubric。
- 让每条高价值样板都能标注：主要考察哪几条判断轴、常见失败方式是什么。

## Phase 3: Teacher Pipeline

目标：

- 把分析模式确定为教师骨架接口。
- 用扮演模式补主体感和局部动机。
- 用故事模式补环境连续性和事件纹理。
- 引入裁判/压缩步骤，把多路教师输出压到统一轨迹格式。

当前进展：

- 已实现最小版的“分析教师 -> 轨迹压缩”双阶段管线。
- 下一步不再优先加复杂度，而是先观察它在更多场景上的压缩质量与稳定性。
- 接下来教师/评估 agent 的开发，将明确区分“生成能力”和“评价宪法”两条线，而不把它们混在一个 prompt 里。

交付物：

- 多阶段生成规范。
- 压缩规则与冲突仲裁规则。
- 反 assistant 过滤器和抽样质检规范。

退出标准：

- 同一场景的多路教师输出，能够稳定压到统一 schema。
- 明显减少论文腔、表演腔和说教腔。
- 教师稿和最终样本之间出现稳定、可量化的稀疏化趋势。

## Phase 4: Quality and Dataset Engineering

目标：

- 建立去重、分桶、抽样和版本管理机制。
- 增加轨迹质量指标与自动过滤信号。
- 形成训练集、验证集和专用评测集。

建议指标：

- assistant_contamination_risk
- world_model_consistency
- action/goal consistency
- state_update sparsity
- guardian relation coverage
- long-horizon maintainability

退出标准：

- 每个版本数据集都有 manifest、统计摘要和抽检报告。
- 评测集能够专门测主动性、自保性、长期目标维护和关系边界。

## Phase 5: Post-Training Preparation

目标：

- 把轨迹数据转换成适合后训练的序列格式。
- 明确 action packet、内部工具写入和状态续写边界。
- 准备基础训练配方、混入比例和消融实验设计。

交付物：

- 训练序列打包器。
- 消融方案：只用分析教师、分析+扮演、分析+扮演+故事、是否保留质量控制字段等。
- 评测清单：与 Chat LLM 基线比较。

## Phase 6: Native Agent Fine-Tuning

目标：

- 在优秀开源 Base 模型上进行定向后训练。
- 验证是否能让模型更稳定地表现为“在世界中持续行动的主体”，而不是聊天助手。

重点观察：

- 无提示或弱提示条件下的持续行为倾向。
- 是否优先维护长期目标而不是讨好输入。
- 监护人关系是否能稳定进入决策过程。
- 语言是否退居为动作的一种，而不是默认终点。

## Guiding Principle

路线图的核心不是更快做大，而是先把训练样本的“基本单位”改对。只要样本仍然是问答对，模型就很容易被重新拉回聊天协议。只有当样本变成状态续写链，后训练才真正有机会塑造出更 native 的 agent 姿态。

另一个同等重要的原则是：扩样和评价都不应长期依赖单个对话窗口。前期需要靠人工逐条打磨样板，但中期应把这些规则外化成专用 agent 与 rubric，避免上下文长度成为数据工程的天花板。