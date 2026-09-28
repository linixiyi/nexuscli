# F5 /compact 手动压缩（入口层）— 实现简报（第一波）

必读：`design/nexuscli-context.md` context/manager 节；`src/nexuscli/context/manager.py` 全文（257 行）；`design/refs-notes.md` §5。

## 边界（本波只做 manager 入口，不动 repl.py）
只改 `src/nexuscli/context/manager.py` 与 `tests/test_context.py`（扩充既有文件；若无则新建——先 ls tests/ 确认）。`/compact` 的 REPL 命令接线是第四波集成车道的事。

## 设计规格
在 `ContextWindowManager` 上加公开方法（确定性压缩，**不调 LLM**）：

```python
def compact_now(
    self,
    messages: list[Message],
    *,
    focus: str = "",
) -> CompressionResult:
    """手动压缩：无论是否超阈值都压缩一次，返回压缩后消息与统计。"""
```
实现要点：
1. 空历史（0 或 1 条）→ 直接返回 `CompressionResult(list(messages), 0, 0, compressed=False)`（空历史安全处理，不抛异常）。
2. 复用现 `prepare()` 的骨架但**跳过阈值判断**：`_recent_boundary` 定近端保留（`min_recent_messages` 条 verbatim）；older 侧走 `_summarize`。
3. focus 非空时，在摘要首行注入保留指示（置于 `<conversation-summary trust="untrusted-session-data">` 头两行之后）：`Focus: {focus}` + 一句 "Preserve information related to this focus first."。注意 `_summarize` 现在把行数额度按消息均分——focus 行不计入 per-message 额度，总长仍受 `summary_max_chars` 约束（超长截断时保 closing tag 的既有行为保持）。
4. 压缩后仍跑 `_truncate_tool_payloads` 与 `_shrink_summary`（照抄 prepare() :97-102 顺序），保证手动压缩产物与自动压缩同等安全。
5. `estimated_tokens_before/after` 用 `_estimate_request(messages, "", [])`（无系统提示/工具定义上下文时的估算即可，docstring 注明）。
6. **不改 `prepare()` 的任何行为**（自动压缩回归零风险）；新方法内部可以抽小的共享私有helper（如 `_build_compacted(older, recent, focus)`），但避免大重构。

## 测试 `tests/test_context.py` 扩充
覆盖：手动压缩返回前后 token 统计且 after < before（构造 30+ 条长消息）；`compressed=True` 且 `summarized_messages` = older 条数；focus 主题出现在摘要文本中（含 "Focus:" 前缀行）；历史条数下降（len(result.messages) < len(messages)）；近端 `min_recent_messages` 条保持 verbatim（原文比对）；空历史与单条历史安全处理（compressed=False 不抛异常）；`prepare()` 自动压缩行为不变（回归：阈值内不压缩用例已有则保持，无则补一条）。
