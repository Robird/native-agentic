# Sprint 2 Brief: Shrink User Surface

## 1. 目标

本 sprint 的目标是收缩“日常使用者真正需要面对的 surface”，而不是减少系统底层能力。

重点是两层分离：

- daily vs expert 配置面
- daily artifacts vs trace artifacts 输出面

这个 sprint 可以调整文档、manifest 和默认输出策略，但不应破坏当前 canonical sample unit。

当前要明确把下面这些对象视为可演化 UX surface，而不是冻结接口：

- `pipeline_manifest.json` 的形状
- `interfaces/` 的默认写出策略
- 空 repair 时 `repair_summary.*` 的物化策略

因此，这个 sprint 的判断标准首先是“方向是否更合理”，而不是“旧形状是否原封不动保留”。

## 2. 背景依据

先读：

- [docs/design-simplification-audit.md](docs/design-simplification-audit.md)
- [docs/quick-start.md](docs/quick-start.md)
- [README.md](README.md)

当前问题主要体现在：

1. 文档已经把日常工作流收敛到少数命令与少数结果文件，但代码和输出树默认仍暴露大量 trace 产物。
2. pipeline manifest 的职责更像“总索引 + 部分 stage 元数据重复”，未来容易漂移。
3. `interfaces/`、空 repair summary 等输出物，对日常 smoke 来说噪声偏大。
4. 日常用户真正常用的是少数 knobs，但文档和代码层对 expert overrides 的权重仍偏高。

## 3. 本 sprint 的预期结果

完成后应该达到：

- 日常用户能更快判断“跑哪条命令、看哪些文件”。
- pipeline 顶层结果更像 stable index，而不是所有 trace 的平铺入口。
- trace 能力仍保留，但默认不再和 daily path 同权。
- 文档、manifest 和默认输出策略的心智模型更一致。

## 4. 优先事项

### 4.1 明确 daily presets

建议把当前 quick start 中的三类日常工作流正式化：

- smoke
- inspect
- repair

不一定必须通过新脚本实现，也可以先通过文档约定、manifest 字段或轻量封装实现。

### 4.2 压薄 pipeline manifest

当前 [write_pipeline_manifest()](scripts/run_sample_pipeline.py#L1182) 承载的内容较多。

建议让它更像稳定索引页，只保留：

- run 级摘要
- 关键 daily knobs
- stage manifest refs
- 关键输出 refs

而不是重复 stage 细节。

### 4.3 处理 `interfaces/` 输出策略

需要评估 [docs/sample-packet-and-agent-interfaces.md](docs/sample-packet-and-agent-interfaces.md#L95) 中的正式接口留档价值，与日常输出噪声之间的权衡。

优先考虑：

- 改成 opt-in
- 或者至少在 daily 视角中降权，不再默认强调

### 4.4 处理空 repair 情况下的输出噪声

当前在 `AUTO_REPAIR=1` 但没有实际 repair attempt 时，仍可能产生 repair summary 体系。

需要决定：

- 是否应该默认不写
- 或折叠进 pipeline summary / manifest
- 或仅在实际发生 repair 时才物化

### 4.5 文档分层

需要明确把：

- daily knobs
- expert overrides
- daily artifacts
- trace artifacts

分别放到文档的不同层级，不再混写。

## 5. 禁改项

本 sprint 默认不要主动做：

- 修改 `sample_packet_v1` 结构。
- 重写 generator / evaluator 的整体职责边界。
- 引入完整配置框架。
- 新增复杂 workflow engine。
- 扩展 repair 能力到更复杂的策略搜索或无界循环。

## 6. 建议的子任务切片

### Slice A

目标：先梳理并收敛 daily presets 与文档分层，让 daily knobs / expert overrides 有清晰边界。

### Slice B

目标：压薄 pipeline manifest，让它更像稳定索引，而不是细节总表。

### Slice C

目标：处理 `interfaces/` 输出策略，评估 opt-in 或降权方案。

### Slice D

目标：处理空 repair 情况下的 summary 产物策略，并同步文档说明。

## 7. 验收标准

至少满足：

1. 日常路径与追踪路径的边界在文档和输出面上都更清楚。
2. pipeline manifest 顶层明显更稳定、更像索引。
3. 日常用户不再被大量 trace 输出默认吸引注意力。
4. `sample_packet_v1` 与当前 repair 语义保持稳定。

补充说明：

- 本 sprint 不要求 manifest、interfaces 开关或空 repair summary 的旧形状被严格保留。
- 只要新的默认行为更少噪声、更符合 daily-first 的方向，并且文档与行为对齐，就应视为正向演化。

## 8. 验证建议

优先做：

1. 文档与代码的一致性检查。
2. `get_errors` 和静态验证。
3. 用现有结果目录做局部检查，确认 manifest / summary 形状符合新约定。

除非主代理明确要求，否则不要默认跑真实 DeepSeek live smoke。

同时不要把“保持旧形状”当成验证目标本身；这里更重要的是：

- 新形状是否更符合 daily-first 的索引心智模型。
- 新的默认行为是否减少误导性输出。
- 文档说明是否与实际物化行为一致。

## 9. subagent 回传要求

要求 subagent 明确返回：

1. 本轮缩掉了哪个用户侧或输出侧复杂度。
2. 是否影响现有 daily 命令和结果路径。
3. 改动是否引入兼容性风险。
4. 下一步最自然的切片是什么。