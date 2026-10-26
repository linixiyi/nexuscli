# 记忆、动态 Prompt 与上下文压缩

> 本文是 [README](../README.md) 深入文档索引「记忆、动态 Prompt 与上下文压缩」条目的完整版。

NexusCLI 把记忆分成三层：

- 短期记忆：当前 thread/session 的原始消息、工具调用和工具结果
- 静态长期记忆：`AGENTS.md`、`NEXUS.md`、`.nexuscli/NEXUS.md` 及自定义 prompt 文件；人工维护、可版本控制；`/init [notes]` 可让 Agent 检查工作区并生成或增量更新根 `AGENTS.md`，写入经 write_file 审批
- 动态长期记忆：按项目 scope 隔离的 SQLite 记录；包含 kind、source、importance、confidence、TTL、访问次数、内容哈希，以及实体标签、事件时间 `event_at` 和时态层 `temporal_kind`

动态记忆不会再无条件取"最近 8 条"。每个请求会按当前问题自动召回 Top-K，并把结果放进明确标注为 untrusted data 的动态 Prompt；模型觉得候选不足时，还可以调用 `search_memory` 深搜。

**召回：多信号融合**。相关性不再只看词面——每次召回对 scope 内候选并行打三路内容分：词法覆盖（权重 0.40）、BM25（0.25，在召回 batch 上内存现算词频与文档频次，复用 `rag/code_index.py` 提升为模块级纯函数的 `bm25_score`，按查询词 idf 总量归一到 [0,1]，不读代码索引文件）与实体重合（0.15，`save_memory`/`search_memory` 的可选 `entities` 参数优先，缺省用轻量启发式抽取：引号词、连续大写词、`pyproject.toml` 式标识符、camelCase、`@handle`）。三路按权重加权并归一到 0.80 的内容预算，再叠加 importance（0.08）、confidence（0.06）与 hotness（0.06）；三路内容分全零的条目直接记 0 分，metadata 再高也不会把无关记忆顶进结果。信号源是可替换的 `MemorySignal` 接口：`VectorSignal` 稠密向量位已留好但默认缺席（仓库不引 embedding 依赖），需要时经 `MemoryManager(signals=...)` 注入，激活信号的权重会自动重归一，排序尺度不随插拔回路数漂移。

**召回：时间感知分层**。每条记忆带 `temporal_kind`（current_state / past_event / future_plan）与可选事件时间 `event_at`，动态 Prompt 的记忆行也加 `temporal=` 标注；查询的时态意图用中英文关键词表判别（"plan/接下来"→未来、"ago/上次"→过去，过去与未来标记并存时不做时态加权），意图与条目层级错配时融合分乘 0.85，已过 `event_at` 的 future_plan 按 past_event 匹配。旧库升级由 schema 迁移（`user_version` 门控，一次性）回填：旧行按 kind 映射（fact→past_event，preference/constraint/decision/correction→current_state），实体由同一启发式补齐。

**召回：OpenViking 搬运件**。hotness 衰减信号搬运自 OpenViking（AGPL-3.0，commit 9a57854）`retrieve/memory_lifecycle.py` 的 `hotness_score`——sigmoid(访问频次) × 指数时间衰减，本仓半衰期取 30 天，替代旧的 recency/access 打分对；文件保留上游版权头并注明 Ported from，公式未改动。

**写入：ADD-only 收窄**。同内容哈希再次到达不覆盖既有声明——kind、source、expires_at、temporal_kind 保持原值，只累积不变更；importance/confidence 取 max、实体取并集、`event_at` 仅在为空时补、`updated_at` 照常刷新（访问信号不算内容覆盖）。去重、TTL 与容量淘汰语义不变，写入端仍拒绝空值/超长值。`save_memory` 接受 `entities`/`event_at`/`temporal_kind` 参数，`search_memory` 接受 `entities`/`temporal_kind`，REPL `/save` 支持 `kind=` / `temporal=` / `event_at=` / `entities=` 前置选项。

Prompt 分为可缓存的静态前缀和逐请求重建的动态后缀。静态前缀承载身份、规则和项目指令；动态后缀承载当前时间、cwd、模型、工具以及与当前问题相关的记忆。

可用输入预算按 `context_window - max_output_tokens - reserve_tokens` 计算。默认在该预算的 80% 触发压缩，压到 55% 左右，为后续输出、工具结果和无 tokenizer 估算误差留出空间。压缩摘要只属于短期会话，不会自动晋升为长期记忆。

达到压缩阈值时，压缩流程会先做一步无损的微压缩（microcompact）：把较旧回合的工具结果内容就地替换为固定占位符，消息条数与结构保持不变，最近 5 条工具结果原文保留；若清除后已回到预算内且节省达标，就不再产生摘要。清除后仍超限或节省不足时，才会继续走上述有损摘要路径。该行为随自动压缩启用，不新增配置项。

压缩摘要的角色约束：摘要必须放 **user** 角色——严格交替网关会 outright 拒绝 assistant 开头或 assistant 连续的请求（GLM 实测 HTTP 400 code 11133）。

除了自动压缩，REPL 里可随时手动压缩（`/compact [focus]`，见 [repl-commands.md](repl-commands.md)）。

**provider 超限自动压缩重试**：压缩阈值基于无 tokenizer 的估算，恢复的长会话或超大工具结果可能让请求越过估算直接撞上 provider 的真实 context 上限。这种报错（`llm/base.py` 的 `is_context_overflow_error` 按短语识别，覆盖 OpenAI「maximum context length」/`context_length_exceeded` 与 Anthropic「prompt is too long」等主流报文）不会立刻让回合失败：主循环先用与 `/compact` 相同的手动压缩器压一次历史并重试同一回合，只重试一次，通知以 `text_delta` 流出（「Provider reported a context-length overflow; compacting …」）。两种情况仍照常报错：历史已无可压内容（`compact_now` 报告未压缩），或重试后二次溢出。该行为随 `features.context_compression` 开关；如实边界：只接在主 Agent 的 ReAct 循环上，`task` 委派的子代理路径未接——子代理撞超限仍直接报错。新 provider 的报文措辞不在短语表里时不会触发重试，需要按需补充短语。
