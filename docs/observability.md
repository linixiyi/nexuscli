# 可观测性与调试

> 本文是 [README](../README.md)「🔭 可观测性与调试」章节的完整版。

本文覆盖四块彼此独立、可单独开关的观测能力：trace_id 关联、OpenTelemetry 遥测、模型 IO 调试落盘、本地 usage 落库。

## trace_id：审计 ↔ LLM 请求关联

每个 REPL 进程会话有一条 16 位十六进制的 trace id（实现：`src/nexuscli/observability.py`，首个消费者惰性生成，之后进程内稳定）：

- **审计日志**：执行器写每条审计记录时附 `trace_id` 字段（`policy/audit_log.py`；REPL 里 `/audit [N]` 即可看到）。无 trace id 的调用方字段为空串，老记录格式兼容。
- **LLM 请求头**：OpenAI-compatible 客户端在 `chat` 请求上附 `X-Request-ID: <trace_id>`（`llm/openai_compatible.py`）。刻意边界：`list_models` 走的 `/models` 请求不带该头，只有 chat 路径在切片内。

同一会话内「这次模型调用 ↔ 这些工具审计」可用同一 id 对起来。作用域是**进程内**：跨进程传播明确不在切片内；子代理与主代理同进程，因此共享同一条 id。

## OpenTelemetry 遥测（可选依赖）

配置：

```json
{ "telemetry": { "enabled": false } }
```

`telemetry.enabled` 默认 `false`。span 只在「开关打开 **且** 可选依赖 `opentelemetry-api/sdk` 已安装（`uv sync --extra telemetry`）」时才真正产生；任一条件不满足，全链路 no-op——调用点无分支、行为与没有遥测代码的构建逐字节一致（实现：`src/nexuscli/telemetry/tracing.py`）。

两类 span：

| span | 属性 |
|---|---|
| `llm.chat`（`llm/openai_compatible.py` 的 chat 流程） | `nexuscli.trace_id`、`nexuscli.llm.model`、`nexuscli.llm.provider` |
| `tool.call`（`tools/executor.py` 的工具执行） | `nexuscli.trace_id`、`nexuscli.tool.name`、`nexuscli.tool.outcome`（`ok`/`error`，在 span 结束前写入） |

**支持边界（如实说明）**：

- 内置实现**没有 OTLP exporter，未接真实 collector**，不做任何网络导出；生产路径直接使用 OTel 全局 `TracerProvider`（不注入时即默认 `NonRecordingSpan`，等于不导出）——这是可复用 OTel instrumentation 的标准姿态。需要导出时，可在自己拉起的进程里先注册全局 `TracerProvider`（如 OTLP exporter 指向你自己的 collector）。
- 仓库内的验证口径是 **in-memory span exporter** 的单元测试（`tests/test_telemetry.py`：断言 span 名称、属性、SDK 缺失时 no-op、工厂接线产出 chat span），未做真实 collector 的端到端验证。
- 未移植（对比 ZCode）：agent_turn/agent_step/compaction 多层 span 层级、跨进程 span 传播、采样策略、错误脱敏器。

## 模型 IO 调试落盘（`llm.debug_dump`）

```json
{ "llm": { "debug_dump": true } }
```

默认 `false`。开启后（实现：`llm/openai_compatible.py`）：

- 每回合追加两行 JSONL 到 `~/.nexuscli/debug/llm/<YYYY-MM-DD>.jsonl`：请求行（在首个流事件前落盘，含 `ts`/`direction: request`/`provider`/`model`/`url`/`headers`/`payload`）与响应行（流结束后落盘，含聚合 `text`、`tool_calls`、`usage`）
- **敏感头脱敏**：`authorization`、`proxy-authorization`、`x-api-key`、`cookie`、`set-cookie` 的值替换为 `<redacted>`；payload 为完整请求体（model/messages/tools 及采样参数等），不含凭据
- 超时或 HTTP 状态错误的回合落一行 `direction: response` 的错误摘要；连接类错误提前返回的回合只有请求行（每回合至少留下请求记录）
- 落盘 best-effort：任何写失败被吞掉，绝不打断对话回合

**注意**：请求头脱敏 ≠ 内容脱敏——`messages` 正文里可能有你的代码与文件内容，该文件只在可信机器上临时开启排查，用完关闭。

## 本地 usage 落库（`/usage stats` 的数据源）

REPL 每完成一个回合，把一行用量写入 SQLite `~/.nexuscli/usage.db`（表 `turn_usage`：时间戳、session_id、model、provider、input/output/total tokens、cost_usd、cost_cny；实现：`llm/usage_store.py`）。SQL 全部走绑定参数，落库 best-effort——磁盘错误、库被锁、缺字段都不会打断回合。

**覆盖面边界（如实披露）**：只有 REPL 的**普通回合**与**自定义命令回合**会落库。以下回合不产生记录：

- `/init`、`/plan`、`/workflow`、`/expert` 派发的回合（`repl._record_turn_usage` 只挂在主循环两个回合分支上）
- 单次 CLI 模式（`nexuscli -p`）的执行
- 落库值取主代理自身的 `last_usage`——子代理（前台 `task` 或后台 `task`）是独立的 Agent 实例，其消耗**不会**合并进主回合的落库行，也没有自己的行

因此 `/usage stats` 的统计值反映的是 REPL 直连对话中主代理的用量，不是含子代理的全局账单。
