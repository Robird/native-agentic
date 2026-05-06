# Sprint 1 Brief: Thin Orchestrator

## 1. 目标

本 sprint 的目标是把 [scripts/run_sample_pipeline.py](scripts/run_sample_pipeline.py) 从“厚 orchestrator”收缩成更接近“薄编排层 + 独立领域内核”的结构。

优先处理的是：

- 内部职责边界。
- 参数爆炸。
- repair / initial path 的重复装配。
- 类型不清晰的中间结果结构。

这个 sprint 不追求新能力，也不追求改变用户工作流。

## 2. 背景依据

先读：

- [docs/design-simplification-audit.md](docs/design-simplification-audit.md)
- [docs/sample-packet-and-agent-interfaces.md](docs/sample-packet-and-agent-interfaces.md#L95)

当前主要复杂度来源：

1. [build_sample_packet()](scripts/run_sample_pipeline.py#L1020) 参数太多，且 provenance 引用与核心输入混在一层。
2. [execute_repair_attempt()](scripts/run_sample_pipeline.py#L626) 与 [main()](scripts/run_sample_pipeline.py#L1311) 中存在重复的路径和装配逻辑。
3. `Config` 同时承载 options、路径布局、阶段常量与 schema 留档信息。
4. packet / review 纯逻辑与 orchestrator 编排逻辑没有明显边界。

## 3. 本 sprint 的预期结果

完成后应该达到：

- `run_sample_pipeline.py` 更短、更像编排层。
- packet / review 逻辑可以被独立阅读和局部验证。
- provenance、attempt context、repair result 不再依赖大而散的匿名字典和长参数列表。
- 不改变 CLI、主要环境变量、`sample_packet_v1` 结构和 repair 语义。

## 4. 允许做的事

优先顺序建议如下。

### 4.1 抽出小结构

建议引入显式小结构，用来代替当前散参数或大字典：

- `ProvenancePaths`
- `AttemptContext`
- `RepairAttemptResult`

形式可以是 dataclass、TypedDict 或同等清晰的轻量结构。

### 4.2 提取 provenance 装配逻辑

建议提取一个统一函数，例如：

- `resolve_provenance_paths(...)`

目标是让初始路径与 repair 路径共享同一套 request/raw/input/source_refs 装配逻辑。

### 4.3 抽出 packet / review kernel

优先考虑迁移下列逻辑到独立模块：

- `resolve_feedback_and_failures()`
- `review_state()`
- `validate_packet()`
- `build_sample_packet()`

允许保留少量 orchestrator 相关依赖，但要尽量避免把子进程调度逻辑带进去。

### 4.4 收缩 `Config`

优先做低风险收缩：

- 把固定阶段 run id 提为模块常量。
- 减少明显恒定、仅用于留档的字段在主流程中的存在感。

如果 `Config` 两层拆分会显著扩大改动面，本 sprint 可以先不做完全拆分。

## 5. 禁改项

本 sprint 默认不要主动做：

- 修改 `sample_packet_v1` schema。
- 改写 `feedback_protocol` 或 `failure_taxonomy` 的语义。
- 改变 `AUTO_REPAIR`、`MAX_REPAIR_ATTEMPTS`、`STOP_ON_NO_PROGRESS` 等行为语义。
- 调整用户可见的主命令入口。
- 扩展新功能或新 policy。

## 6. 建议的子任务切片

### Slice A

目标：引入 `ProvenancePaths`、`AttemptContext`、`RepairAttemptResult` 等轻量结构，并让关键调用点用起来。

### Slice B

目标：提取统一的 provenance 路径装配函数，消除 repair path 与 initial path 的重复装配。

### Slice C

目标：把 packet / review kernel 从 orchestrator 中抽出去，并保持主流程行为不变。

### Slice D

目标：收缩 `Config` 的明显恒定部分，并做小范围文档同步。

## 7. 验收标准

至少满足：

1. [scripts/run_sample_pipeline.py](scripts/run_sample_pipeline.py) 的主流程边界比当前更清晰。
2. `build_sample_packet()` 的调用点明显缩短，或其参数层次明显更清楚。
3. repair 路径和初始路径不再各自重复推导一整套 provenance 引用。
4. 不改变当前 canonical sample unit 和 repair 语义。

## 8. 验证建议

默认优先：

1. 对修改文件运行 `get_errors`。
2. 做局部导入或无 API 的窄验证。
3. 如需额外验证，可做 focused static checks，例如 `python3 -m py_compile ...`。

除非主代理明确要求，否则不要默认跑真实 DeepSeek live smoke。

## 9. subagent 回传要求

要求 subagent 明确返回：

1. 改了哪些文件。
2. 哪个复杂度点被消掉了。
3. 做了哪些验证。
4. 是否引入新的集成风险。
5. 下一个最自然的切片是什么。