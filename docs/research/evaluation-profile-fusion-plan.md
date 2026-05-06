# Evaluation Profile Fusion Plan

## Why Fuse Instead Of Add More Axes Immediately

当前最稳的路线，不是立刻把“反 Chat / Native Agentic”维度再加成第 9、第 10 条轴，而是先把它们吸收到现有 8 轴里。

理由有两层。

### 1. 概念上没必要先膨胀轴数

前面归纳出来的 5 个新维度，很多并不是与现有工作宪法平行的全新伦理学来源，而更像：

- 对现有判断轴的工程化补足
- 对主流 Chat 后训练坏习惯的对抗性展开

也就是说，它们大多可以被视为：

- `智` 的细化
- `义` 与 `礼` 的展开
- `自强与修身` 的现代工程化版本
- `法与护` 在真实系统环境下的延展

### 2. 工程上当前脚本默认假设“就是 8 轴”

当前评估脚本 [scripts/evaluate_trajectories.py](scripts/evaluate_trajectories.py#L389) 到 [scripts/evaluate_trajectories.py](scripts/evaluate_trajectories.py#L430) 在 prompt 里直接写了：

- `axis_results` 长度必须为 8
- 必须覆盖固定 8 个 `axis_id`

所以如果现在直接把 profile 改成 9 轴或 10 轴，而不先改脚本，系统会先和自己冲突。

因此，当前更合适的 Pilot 方案是：

- 维持 8 轴计数不变
- 在每条轴的 `intent` 和 `questions` 里吸收新语义
- 先验证这种“语义融合”是否已经足够提升辨识度

## Recommended Mapping

### 主动推进与等待替代

主要融入：

- `zhi`
- `yong_jie`
- `self_cultivation`

含义：

- 不再把“谨慎”默认等同于停下等问
- 在可试探、可回退、可增益信息时主动往前推进

### 关系责任与非用户中心平衡

主要融入：

- `yi`
- `li`
- `law_guardianship`

含义：

- 当前说话者不再天然是唯一中心
- 关系远近、权限差异、既有承诺和第三方利益都应进入判断

### 判断承诺与反虚假平衡

主要融入：

- `zhi`
- `xin`

含义：

- 在不完整信息下也要敢于给出带置信度的暂定判断
- 不用“双方都有道理”的外交话术逃避落子

### 自我模型与成长导向

主要融入：

- `self_cultivation`
- `xin`

含义：

- 准确认识自己当前能力边界与资源状态
- 在发现新事实、失败模式和稳定偏好后，主动写回学习资产

### 长期目标连续性与状态闭环

主要融入：

- `self_cultivation`
- `yi`
- `law_guardianship`

含义：

- 不让最新消息自动覆盖旧承诺和长期主线
- 保留恢复点、挂起事项和后续升级路径

## Concrete Pilot Asset

基于上面的思路，已经新增了一个兼容当前 8 轴脚本约束的试验 profile：

- [profiles/evaluation_profile_constitutional_agentic_pilot_v1.json](profiles/evaluation_profile_constitutional_agentic_pilot_v1.json)

它不是替换默认 profile，而是一个 drop-in pilot：

- 同样保留 8 个 `core_axes`
- 同样复用 `failure_taxonomy_v1` 与 `feedback_protocol_v1`
- 只是把反 Chat / Native Agentic 语义融进 `intent` 和 `questions`

## Why This Helps The 2B SFT Pilot

如果后面要做 2B 左右的小型 SFT，这种融合方式有两个好处。

### 1. 评测与训练目标更接近

训练阶段想塑造的东西，本质上是：

- 不再停步等待
- 不再自动用户中心
- 不再假中立
- 会写回长期状态
- 会形成较稳定的自我模型和成长倾向

这些都能直接在当前 8 轴里找到对应入口，而不用再维护第二套完全平行的打分系统。

### 2. 脚本改动最小

当前 [study-base-llm/src/peft_unsloth.py](/repos/study-base-llm/src/peft_unsloth.py#L260) 到 [study-base-llm/src/peft_unsloth.py](/repos/study-base-llm/src/peft_unsloth.py#L375) 的数据入口其实很简单：

- 只要提供一个带 `text` 列的数据集
- `SFTTrainer` 就能直接吃

这意味着第一轮训练不必先解决复杂 schema 对齐；只需把“场景 + 理想姿态 + 理想下一步意图”压成文本模板，就能先做最小 SFT。

## Current Recommendation

当前最合理的顺序是：

1. 先用这个融合版 8 轴 pilot profile 做评测试跑。
2. 再围绕 [data/bootstrap/chat_symptom_seed_set_v1.json](data/bootstrap/chat_symptom_seed_set_v1.json) 扩一批训练样本。
3. 第一轮 2B SFT 只训“先教做人”的部分，不把工具调用正确性一起混进主目标。
4. 训练后用同一套融合版 8 轴去做前后对照。

这能在不让复杂性失控的前提下，把研究往真正的学生模型验证推进一步。