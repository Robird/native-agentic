# Design Simplification Audit

这份盘点的目标不是提出新能力，而是寻找“当前框架哪里可以压实、收敛、减少心智负担”。

如果后续采用“主代理负责调度，subagent 负责具体改造切片”的工作方式，请配合阅读：

- [docs/sprints/README.md](docs/sprints/README.md)
- [docs/sprints/sprint-01-thin-orchestrator.md](docs/sprints/sprint-01-thin-orchestrator.md)
- [docs/sprints/sprint-02-shrink-user-surface.md](docs/sprints/sprint-02-shrink-user-surface.md)

本次结论综合了三部分输入：

- 本地代码与文档审阅。
- `claude` 子代理的独立化简审阅。
- `gpt` 子代理的独立化简审阅。

## 1. 总结结论

当前最值得做的，不是继续扩 repair 或 dataset engineering，而是收缩 orchestrator 周边的控制面。

### 1.1 当前阶段的稳定性边界

当前阶段需要区分两类东西：

- 稳定核心 contract：`sample_packet_v1`、generation -> evaluation -> packet join 的核心闭环、以及 repair routing / repair gate 的基本语义。
- 可演化 UX surface：`pipeline_manifest.json` 的形状、`WRITE_INTERFACES` 这类输出策略、以及“空 repair 是否物化 summary”这类默认落盘行为。

当前对第二类的判断标准，不是“是否已经冻结成长期接口”，而是：

- 方向是否更清楚。
- daily path 与 trace path 是否更分层。
- 文档、默认行为和用户心智模型是否更一致。
- 是否减少了“看起来像错误、其实只是没发生”的噪声。

因此，manifest、interfaces 开关和空 repair summary 在这一阶段可以继续演化；只要方向合理，就不必为了维持旧形状而强行保守。

优先级判断如下：

1. 保持 `sample_packet_v1` 作为当前 canonical sample unit，不先动核心资产格式。
2. 优先收缩 `scripts/run_sample_pipeline.py` 的职责边界与参数面。
3. 优先减少默认输出噪声和日常使用者需要记住的配置项。
4. 暂缓新增更复杂的 repair policy、workflow engine 或配置系统。

## 2. 最高优先级发现

### 2.1 Orchestrator 边界已经变厚

文档中对 orchestrator 的定义仍是“最小 orchestrator”，见 [docs/sample-packet-and-agent-interfaces.md](docs/sample-packet-and-agent-interfaces.md#L95)。

但当前 [scripts/run_sample_pipeline.py](scripts/run_sample_pipeline.py#L1) 实际同时承载了：

- 配置解析与环境变量覆盖。
- 生成阶段 / 评估阶段的子进程编排。
- teacher/evaluator input contract 物化。
- fallback failure / feedback 路由。
- sample packet 组装与校验。
- repair instruction 生成、repair attempt 执行、repair gate 与 repair summary。
- pipeline manifest 与 summary 输出。

这说明 orchestrator 已不再只是“薄编排层”，而是混入了大量领域规则。

最小化简建议：

- 先抽出 packet/review kernel，把 `resolve_feedback_and_failures()`、`review_state()`、`validate_packet()`、`build_sample_packet()` 这类纯逻辑从 orchestrator 主文件中拿出去。
- `scripts/run_sample_pipeline.py` 只保留“跑阶段、收产物、调用 kernel、落盘”的薄壳。

### 2.2 `build_sample_packet()` 已成为参数爆炸点

当前 [build_sample_packet()](scripts/run_sample_pipeline.py#L1020) 是最明显的复杂度汇聚点。

它同时接收：

- scenario / row / manifest 级输入。
- teacher/evaluator input 路径。
- teacher/raw/request/evaluation/raw 等 provenance 路径。
- protocol/taxonomy 配置。
- attempt metadata。

这带来两个问题：

1. 调用点太长，初始路径和 repair 路径都需要展开一大串参数。
2. provenance 只是引用信息，却和真正决定 packet 结构的输入混在同一层。

最小化简建议：

- 引入 `ProvenancePaths` 小结构，收拢 request/raw/input 等来源路径。
- 引入 `AttemptContext` 小结构，收拢 `attempt_index`、`attempt_kind`、`auto_repaired`、`repair_origin_sample_id`。
- 让 `build_sample_packet()` 只接收少数高层对象，而不是二十多个散参数。

### 2.3 repair path 被过早耦合进默认 happy path

当前 quick start 已经建议日常使用先 inspect、后 repair，见 [docs/quick-start.md](docs/quick-start.md#L74) 和 [docs/quick-start.md](docs/quick-start.md#L175)。

但代码层面，repair 不只是一个“附加动作”，而是已经强耦合进主 orchestrator：

- repair attempt 执行在 [execute_repair_attempt()](scripts/run_sample_pipeline.py#L626)。
- repair gate、repair summary、repair loop 都在 [scripts/run_sample_pipeline.py](scripts/run_sample_pipeline.py#L1311) 主流程附近完成。

这会让 generation / evaluation 的任何变更，都需要同时顾及 repair 兼容。

最小化简建议：

- 下个 sprint 不再继续扩 repair 能力。
- 先把 repair loop 从 happy path 中模块化，成为明确的第二层控制面。
- 若暂时不改 CLI，则至少先在代码结构上做到 repair 逻辑与初始 pipeline 分块清晰。

### 2.4 配置面宽于日常需要

当前 [load_config()](scripts/run_sample_pipeline.py#L80) 和 [validate_config()](scripts/run_sample_pipeline.py#L142) 暴露并验证了相当多的配置项。

其中一部分对日常使用有价值：

- `RUN_ID`
- `MODEL_PROFILE`
- `SAMPLES`
- `AUTO_REPAIR`
- `MAX_REPAIR_ATTEMPTS`

但另一部分更多是高级覆写或留档用途，例如输入 schema 路径、分阶段 model/env override 等。它们存在工程价值，但不该和日常入口同权。

最小化简建议：

- 先把配置面分成 daily knobs 和 expert overrides 两层。
- 文档层只高亮 daily knobs。
- 代码层把明显恒定的项，例如固定阶段 run id，收回模块常量，减少 `Config` 负担。

### 2.5 输出物默认太多，canonical 与 trace 的边界不够清楚

文档已经把 `sample_packet_v1` 定义为 canonical sample unit，见 [docs/sample-packet-and-agent-interfaces.md](docs/sample-packet-and-agent-interfaces.md#L15)。

但一次 pipeline run 的默认产物仍然很多：

- stage requests/raw。
- stage manifests/summaries。
- interface 输入文件。
- sample packets。
- pipeline manifest/summary。
- 可选 repair attempts 与 repair summaries。

这些追踪物都合理，但“默认全部落盘”会提高日常 smoke 的认知负担。

最小化简建议：

- 把输出物心智模型明确切成两层：daily artifacts 与 trace artifacts。
- daily 默认只强调：`sample_packets.jsonl`、`pipeline_summary.txt`、`pipeline_manifest.json`。
- requests/raw/interfaces/repair_attempts 等统一降级为追踪层或 expert path。

## 3. 最适合优先做的实现优化

这些改动不需要改变外部能力，适合作为下个 sprint 的主干。

### 3.1 抽出 provenance 解析函数

当前初始路径和 repair 路径都在手写 request/raw/input 目录推导，典型位置见 [execute_repair_attempt()](scripts/run_sample_pipeline.py#L626) 与 [main()](scripts/run_sample_pipeline.py#L1311)。

建议：

- 提取一个 `resolve_provenance_paths(...)`。
- 初始 packet 和 repaired packet 都走同一套路径装配。

### 3.2 给 repair result 和 provenance 引用加显式小结构

当前 `execute_repair_attempt()` 返回的是一个大字典，键很多且混合了：

- manifest
- row
- input path
- instruction path
- reused_teacher_analysis 标志

建议：

- 给 repair result 定义小 dataclass 或 TypedDict。
- 先加类型和边界，不急着做大重构。

### 3.3 让 `Config` 更像 options，而不是一切路径的总表

当前 `Config` 同时承担：

- root/run 路径布局。
- schema 路径。
- 子进程脚本位置。
- repair policy 参数。
- stage 运行 id。

建议：

- 先把固定不变的阶段 run id 提为模块常量。
- 中期再考虑拆成 `RunPaths` 和 `RunOptions` 两层。

### 3.4 压薄 manifest 顶层

当前 [write_pipeline_manifest()](scripts/run_sample_pipeline.py#L1182) 同时记录了：

- stage refs
- model/profile 元信息
- auto_repair 策略
- output files
- schema 路径

它本身有价值，但未来容易和 stage manifest 重复漂移。

建议：

- 把 pipeline manifest 定位成“稳定索引页”，主要保存 run summary、关键 knobs 和 stage manifest refs。
- 详细 stage 元数据留在各自 stage manifest。

## 4. 明确暂缓的事项

### 4.1 暂不改 `sample_packet_v1`

当前 canonical sample unit 已经清晰，且是后续回流和评审的锚点，见 [docs/sample-packet-and-agent-interfaces.md](docs/sample-packet-and-agent-interfaces.md#L15)。

化简的重点应该是外围控制面，不是先动核心资产格式。

### 4.2 暂不扩 repair 到更复杂的策略搜索

路线图已经说明，当前阶段更应该先观察 progress signal 与停机边界，而不是继续叠复杂度，见 [docs/roadmap.md](docs/roadmap.md#L87)。

### 4.3 暂不上完整配置框架

当前问题不是“没有配置系统”，而是“默认 surface 过宽”。

因此优先顺序应该是：

1. 收缩默认参数面。
2. 降级 expert override。
3. 只有在参数面稳定后，再考虑是否需要更强的配置层。

### 4.4 暂不把生成器和评估器重新并成一个大脚本

当前 quick start 已经清楚区分了：

- 只看生成
- 只看评估
- 看整链路

这对定位退化是有价值的，见 [docs/quick-start.md](docs/quick-start.md#L74)。

## 5. 建议的 sprint backlog

### Sprint 1：纯收敛，不改外部行为

目标：降低维护风险，减少 orchestrator 主文件厚度。

建议项：

1. 抽出 packet/review kernel。
2. 提取 `resolve_provenance_paths(...)`。
3. 为 provenance、attempt context、repair result 引入小结构。
4. 把固定阶段 run id 提为模块常量。
5. 收缩 README 中对日常用户无帮助的重复说明，把 quick start 继续作为主入口。

预期收益：

- 主 orchestrator 更薄。
- 修改 routing 或 packet 逻辑时，回归面更小。
- repair 与初始路径的重复代码减少。

### Sprint 2：收缩使用面和输出面

目标：让“日常使用”和“深度追踪”明显分层。

建议项：

1. 定义三个正式 daily presets：smoke、inspect、repair。
2. 文档中把 daily knobs 与 expert overrides 分组。
3. pipeline manifest 瘦身为稳定索引。
4. 评估 `interfaces/` 是否应改成 opt-in 输出。
5. 评估空 repair 情况下，是否仍需要默认写 repair summary 文件。

预期收益：

- 使用者更容易判断跑哪条命令、看哪些文件。
- output tree 更安静，默认 run 更像“看结论”，而不是“倒一整包 trace”。

## 6. 结论

如果下个 sprint 只做一件事，建议优先做：

把 `run_sample_pipeline.py` 从“厚 orchestrator + 领域逻辑 + repair 控制面 + 输出聚合器”收缩成“薄 orchestrator + 独立 packet/review kernel”。

这一步不增加任何新能力，但会明显降低后续每次扩样、调 routing、改 repair policy 时的维护成本。