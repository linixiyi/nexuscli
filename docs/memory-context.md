# 记忆、动态 Prompt 与上下文压缩

> 本文是 [README](../README.md) 深入文档索引「记忆、动态 Prompt 与上下文压缩」条目的完整版。

NexusCLI 把记忆分成三层：

- 短期记忆：当前 thread/session 的原始消息、工具调用和工具结果
- 静态长期记忆：`AGENTS.md`、`NEXUS.md`、`.nexuscli/NEXUS.md` 及自定义 prompt 文件；人工维护、可版本控制；`/init [notes]` 可让 Agent 检查工作区并生成或增量更新根 `AGENTS.md`，写入经 write_file 审批
- 动态长期记忆：按项目 scope 隔离的 SQLite 记录；包含 kind、source、importance、confidence、TTL、访问次数和内容哈希

动态记忆不会再无条件取"最近 8 条"。每个请求会按当前问题自动召回 Top-K，并把结果放进明确标注为 untrusted data 的动态 Prompt；模型觉得候选不足时，还可以调用 `search_memory` 深搜。写入端会拒绝空值/超长值，通过规范化哈希去重，并按项目容量淘汰低价值记录。

Prompt 分为可缓存的静态前缀和逐请求重建的动态后缀。静态前缀承载身份、规则和项目指令；动态后缀承载当前时间、cwd、模型、工具以及与当前问题相关的记忆。

可用输入预算按 `context_window - max_output_tokens - reserve_tokens` 计算。默认在该预算的 80% 触发压缩，压到 55% 左右，为后续输出、工具结果和无 tokenizer 估算误差留出空间。压缩摘要只属于短期会话，不会自动晋升为长期记忆。

达到压缩阈值时，压缩流程会先做一步无损的微压缩（microcompact）：把较旧回合的工具结果内容就地替换为固定占位符，消息条数与结构保持不变，最近 5 条工具结果原文保留；若清除后已回到预算内且节省达标，就不再产生摘要。清除后仍超限或节省不足时，才会继续走上述有损摘要路径。该行为随自动压缩启用，不新增配置项。

压缩摘要的角色约束：摘要必须放 **user** 角色——严格交替网关会 outright 拒绝 assistant 开头或 assistant 连续的请求（GLM 实测 HTTP 400 code 11133）。

除了自动压缩，REPL 里可随时手动压缩（`/compact [focus]`，见 [repl-commands.md](repl-commands.md)）。

**provider 超限自动压缩重试**：压缩阈值基于无 tokenizer 的估算，恢复的长会话或超大工具结果可能让请求越过估算直接撞上 provider 的真实 context 上限。这种报错（`llm/base.py` 的 `is_context_overflow_error` 按短语识别，覆盖 OpenAI「maximum context length」/`context_length_exceeded` 与 Anthropic「prompt is too long」等主流报文）不会立刻让回合失败：主循环先用与 `/compact` 相同的手动压缩器压一次历史并重试同一回合，只重试一次，通知以 `text_delta` 流出（「Provider reported a context-length overflow; compacting …」）。两种情况仍照常报错：历史已无可压内容（`compact_now` 报告未压缩），或重试后二次溢出。该行为随 `features.context_compression` 开关；如实边界：只接在主 Agent 的 ReAct 循环上，`task` 委派的子代理路径未接——子代理撞超限仍直接报错。新 provider 的报文措辞不在短语表里时不会触发重试，需要按需补充短语。
