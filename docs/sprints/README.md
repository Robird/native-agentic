# Sprint Briefs

这组文档的用途，是给后续 `runSubagent(agentName=...)` 调度提供稳定上下文。

因为每次 subagent 都从空上下文开始，所以不能假设它“记得”前面对话结论。实际调度时，建议至少给它两份路径：

1. 总盘点：[docs/design-simplification-audit.md](docs/design-simplification-audit.md)
2. 当前 sprint brief：本目录下对应 sprint 文档

## 1. 当前两个 sprint 的定位

- Sprint 1：收缩 orchestrator 内部复杂度，不改外部能力边界。
- Sprint 2：收缩用户侧和输出侧 surface，让日常使用与深度追踪分层更清楚。

对应文档：

- [docs/sprints/sprint-01-thin-orchestrator.md](docs/sprints/sprint-01-thin-orchestrator.md)
- [docs/sprints/sprint-02-shrink-user-surface.md](docs/sprints/sprint-02-shrink-user-surface.md)

## 2. 调度原则

主代理负责：

- 把总目标拆成清晰的任务切片。
- 给 subagent 提供必要文档路径和本轮目标。
- 审核 subagent 结果，决定是否接受、修正或继续切分。
- 负责跨切片集成、验证和最终对用户汇报。

subagent 负责：

- 只在当前切片范围内做最小必要修改。
- 说明实际改动、验证动作和剩余风险。
- 不擅自扩展 sprint 范围。

补充判断：

- 对 `sample_packet_v1`、核心 routing / repair 语义这类稳定 contract，优先保守处理。
- 对 manifest 形状、interfaces 输出策略、空 repair summary 这类 UX surface，优先看演化方向是否合理，不必为了维持旧形状而做过度回归约束。

## 3. 每轮 subagent briefing 的最小组成

建议每次都明确给出：

1. 背景文档路径。
2. 本轮唯一目标。
3. 明确的边界或禁改项。
4. 期望验证方式。
5. 回传格式要求。

建议的 briefing 结构：

```text
请先阅读：
- docs/design-simplification-audit.md
- docs/sprints/<current-sprint>.md

本轮只做：<one concrete slice>

不要做：<out of scope>

验证要求：<static checks / focused smoke / no live API unless needed>

请按以下格式返回：
1. 改了什么
2. 为什么这样改
3. 做了什么验证
4. 还剩什么风险或后续切片
```

## 4. 当前推荐的切片方式

### Sprint 1 推荐切片

1. 抽出 provenance 与 attempt / repair result 小结构。
2. 提取 `resolve_provenance_paths(...)` 与相邻装配逻辑。
3. 抽出 packet / review kernel。
4. 收缩 `Config` 和固定阶段 run id。
5. 小范围文档同步。

### Sprint 2 推荐切片

1. 明确 daily presets 与文档分层。
2. 压薄 pipeline manifest。
3. 处理 `interfaces/` 输出是否改为 opt-in。
4. 处理空 repair 情况下的 summary 输出策略。
5. 统一 daily artifacts vs trace artifacts 的表述。

## 5. 统一禁改项

两个 sprint 中都默认不要主动改：

- `sample_packet_v1` 的核心结构。
- 生成器 / 评估器的整体职责边界。
- 无界 repair、复杂策略搜索、workflow engine。
- dataset engineering 层的大改造。

## 6. 验证偏好

默认优先：

1. 静态校验与 `get_errors`。
2. 局部导入、局部组装或无 API 的窄验证。
3. 只有在主代理明确要求时，才做真实 DeepSeek live smoke。

这样能避免子代理在重构阶段过早消耗 API 并把问题混入外部随机性。

但在 UX surface 相关切片中，还应额外遵循一条：

- 不要把“旧形状是否完全保留”当成最高目标。
- 更重要的是新形状是否让 daily / expert、daily / trace 边界更清楚，且没有制造文档与行为的不一致。