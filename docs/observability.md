# 可观测性与调试

> 本文是 [README](../README.md) 深入文档索引「可观测性与调试」条目的完整版。

本文覆盖四块彼此独立、可单独开关的观测能力：trace_id 关联、OpenTelemetry 遥测、模型 IO 调试落盘、本地 usage 落库。

## trace_id：审计 ↔ LLM 请求关联

每个 REPL 进程会话有一条 16 位十六进制的 trace id（实现：`src/nexuscli/observability.py`，首个消费者惰性生成，之后进程内稳定）：

- **审计日志**：执行器写每条审计记录时附 `trace_id` 字段（`policy/audit_log.py`；REPL 里 `/audit [N]` 即可看到）。无 trace id 的调用方字段为空串，老记录格式兼容。脱敏是键名+值级两道闸：`SENSITIVE_KEYS` 键名匹配之外，值级掩码覆盖 `sk-`/`ghp_`/`AKIA` 令牌形态与 Bash 命令串里的 `Bearer <token>` / JWT 三段式（`tests/test_audit_redaction.py` 含防误伤反例）。
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

### OTLP 导出（`telemetry.otlp`，Langfuse 等，可选依赖）

默认关闭。开启后把上面两类**既有 span** 经 `BatchSpanProcessor` 批量后台导出到任意 OTLP/HTTP 端点（Langfuse、Jaeger 等），span 名与属性零改动——导出只是给既有 span 加一个消费者，不产生新 span（实现：`src/nexuscli/telemetry/otlp.py`，exporter 唯一 import 边界）：

```json
{
  "telemetry": {
    "enabled": true,
    "otlp": {
      "endpoint": "https://cloud.langfuse.com/api/public/otel/v1/traces",
      "headers": { "Authorization": "Basic <base64(pk-lf-…:sk-lf-…)>" },
      "timeout": 10
    }
  }
}
```

- **三个键**：`endpoint`（OTLP/HTTP 端点完整 URL，空串即关闭——这是默认值，只配 `headers`/`timeout` 不开启任何行为）；`headers`（头名→值映射，随每个导出请求发送，Langfuse 的鉴权就是 `Authorization: Basic base64("公钥:私钥")`）；`timeout`（可选，单次导出请求超时，单位**秒**，缺省用 exporter 自带的 10 秒）。
- **安装**：`uv sync --extra telemetry-otlp`。extras 分两档：`telemetry` = 能产生 span（`opentelemetry-api/sdk`），`telemetry-otlp` = 能导出 span（proto-http exporter，含前者依赖）——只装前者时配置了 endpoint 会得到带安装指引的降级警告。
- **生效条件与接线时机**：`telemetry.enabled` 打开 **且** `endpoint` 非空 **且** exporter 已安装。首个真实 span 产生时一次性取用**本进程 `load_config` 已解析的 telemetry face**（即 CLI 实际生效的那份配置，`--cwd` 指向的项目根也按解析结果计；进程从未调用 `load_config` 的嵌入场景才回落「user config + 进程 cwd 项目配置」直读），并把导出器挂到**既有** TracerProvider：宿主自己先注册了全局 SDK provider 的优先复用，否则自建一个并安装为全局；之后变更配置不重新接线，重启生效。
- **端点写法**：exporter 使用显式 `endpoint` 时**不会**自动追加 `/v1/traces`，URL 要写全。Langfuse 云端如上；自托管把 host 换成自己的实例（如 `http://localhost:3000/api/public/otel/v1/traces`）。本切片只做 OTLP/HTTP（proto-http，不引 gRPC），gRPC 端点不适用。

**降级语义（导出是旁路）**：

- **exporter 缺依赖**：生产路径把缺包翻译成带 `uv sync --extra telemetry-otlp` 指引的 `OtlpDependencyError`，并降级为**进程内至多一次**的 `warnings.warn`（`[nexuscli:telemetry]` 前缀）；装了 `telemetry` 组时 span 照常产生，CLI 功能与退出码零影响。
- **端点不可达 / 导出报错**：每批导出失败至多一次警告（进程生命周期内，两类失败共享同一个警告预算），exporter 自带的重试与失败日志被压掉不刷屏；不可达端点的内建重试会拖长 flush/进程退出耗时（秒级），但不改变退出码。
- **默认关闭零感知**：不装 exporter、不配 endpoint 时本节代码零 import、零后台线程、零开销，行为与没有这段代码的构建逐字节一致。

**导出内容与信任边界**：导出载荷就是**既有 span 面**（`llm.chat`/`tool.call` 及其属性，含会话 `trace_id`、模型与工具名等元数据），没有额外面板或脱敏层——这些信息会离开你的机器，把 endpoint 指向哪个平台是你自己的信任决定（这是默认关闭的原因之一）。指标/日志信号、gRPC exporter、采样与重试策略定制均不在切片内。

**支持边界（如实说明）**：

- OTLP 导出是**可选项**（见上节），默认仍不做任何网络导出；生产路径直接使用 OTel 全局 `TracerProvider`（不注入也不配 `telemetry.otlp` 时即默认 `NonRecordingSpan`，等于不导出）——这是可复用 OTel instrumentation 的标准姿态。
- 仓库内的验证口径：span 语义用 **in-memory span exporter** 单元测试断言（`tests/test_telemetry.py`：span 名称、属性、SDK 缺失时 no-op、工厂接线产出 chat span）；OTLP 导出/降级用**进程内假 OTLP HTTP 接收端**（`http.server` 环回线程）驱动真实 exporter 覆盖（`tests/test_telemetry_otlp.py`：导出载荷、缺依赖降级、不可达降级、默认关闭零开销）——环回自测，未对接真实 Langfuse 云端做端到端验证。
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

**与转录 usage 记录是两条独立通道**：除 usage.db 外，每个完成的普通回合还会把一条 `type: usage` 记录行追加进当前会话 JSONL 转录（字段：ts/model/provider/input/output/total/cache_hit/cache_miss/cost_usd/cost_cny；实现：`session/store.py` 的 `SessionWriter.append_usage`）。转录记录是会话内口径：`/resume`、`-c` / `--resume` 恢复时求和为历史基线，`/usage` 与单次 `--json` 的 `session_usage` 读的就是它（历史累计 = 基线 + 本进程累计，两者之和等于该转录的台账）；`/clear` / `/resume` / `/fork` 切换转录时基线与本进程累计同步重置。usage.db 不受转录切换影响，继续按天/按模型聚合全局历史。两条通道覆盖面一致（都只记 REPL 普通回合与自定义命令回合），单次 `-p` 模式两条都不写。
