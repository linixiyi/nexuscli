# 配置

> 本文是 [README](../README.md)「🔧 配置」章节的完整版。

NexusCLI 的配置优先级如下：

1. 内置默认配置
2. `~/.nexuscli/config.json`
3. 项目级 `.nexuscli/config.json`
4. 项目级 `.env`
5. CLI 参数
6. 当前进程环境变量

可以像 Java 项目一样，把 DeepSeek Key 写到项目 `.env` 里：

```dotenv
NEXUSCLI_PROVIDER=deepseek
NEXUSCLI_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=your_key_here
```

也可以使用 NexusCLI 通用 Key：

```dotenv
NEXUSCLI_PROVIDER=deepseek
NEXUSCLI_MODEL=deepseek-v4-flash
NEXUSCLI_API_KEY=your_key_here
```

当前支持的 provider-specific API Key 包括：

| 环境变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek |
| `ZAI_API_KEY` | GLM 官方推荐 |
| `GLM_API_KEY` | GLM |
| `STEP_API_KEY` | StepFun |
| `KIMI_API_KEY` | Kimi |
| `ANTHROPIC_API_KEY` | Anthropic（原生 Messages API provider） |
| `GEMINI_API_KEY` | Google Gemini（原生 generateContent provider，`GOOGLE_API_KEY` 亦可用） |
| `OLLAMA_API_KEY` | Ollama（仅服务端开启 OLLAMA_AUTH 时需要） |

通过命令行临时覆盖 provider 和 model：

```bash
uv run nexuscli --provider deepseek --model deepseek-v4-flash
```

连接本地 OpenAI-compatible 服务：

```bash
NEXUSCLI_PROVIDER=openai-compatible \
NEXUSCLI_BASE_URL=http://127.0.0.1:11434/v1 \
NEXUSCLI_MODEL=qwen2.5-coder \
uv run nexuscli -p "解释这个仓库"
```

原生 Anthropic provider（`NEXUSCLI_PROVIDER=anthropic`）走 Anthropic Messages API：API Key 只从 `ANTHROPIC_API_KEY` 环境变量（或既有 `llm.api_key` 配置路径）读取，默认 base_url 为 `https://api.anthropic.com`，默认上下文窗口 200K。注意两个边界：Anthropic 没有内置价格表，成本估算需要显式配置 `llm.prices`；该 provider 暂不支持图片输入，列表型消息会自动降级为文本。

原生 Google Gemini provider（`NEXUSCLI_PROVIDER=google`）走 Gemini 原生 HTTP（`generateContent`/`streamGenerateContent` SSE）：API Key 优先级为 `llm.api_key` → `GEMINI_API_KEY` → `GOOGLE_API_KEY`，只随 `x-goog-api-key` 请求头发送（不进 URL），默认 base_url 为 `https://generativelanguage.googleapis.com`。模型能力按模型 profile 的能力字段判定（`/model` 选内置 Gemini 模型会带入 `vision: true`；直接写 config 时用 `llm.vision` 显式声明），不做模型名猜测——未知模型一律按保守默认处理（无图片输入、上下文窗口回落 128K，可用 `llm.context_window` 覆盖）。图片输入支持 `@image:` 产生的 data URL（转成 `inlineData`），远程 http(s) 图片 URL 无对应请求形态，会降级为文本占位。Gemini 同样没有内置价格表，成本估算需要显式配置 `llm.prices`。

本地推理服务 provider：`NEXUSCLI_PROVIDER=ollama`（默认 base_url `http://127.0.0.1:11434/v1`）与 `NEXUSCLI_PROVIDER=llamacpp`（对应 llama.cpp 的 `llama-server`，默认 base_url `http://127.0.0.1:8080/v1`），另有统一别名 `provider="local"`——端点有歧义（两种服务端口不同），必须显式给出 `llm.base_url`，适合 vLLM、LM Studio 等其它 OpenAI-compatible 本地网关。三者复用 OpenAI-compatible 的流式与 tool call 协议（含 5xx/429 时自动降级非流式重试），需要显式配置 `llm.model`（如 `qwen2.5-coder:latest`）。本地服务通常无需鉴权：未配置 `llm.api_key` 时请求**不携带 Authorization 头**，配了才以 `Authorization: Bearer <key>` 发送（Ollama 开启 `OLLAMA_AUTH` 时用 `OLLAMA_API_KEY` 环境变量即可）。两个如实边界：本地网关不在内置价格表里，本地推理也不该按云端同名模型计价——成本估算需要显式 `llm.prices`（不配置时成本恒为 `null`，见下文）；默认上下文窗口按 32K 处理，比云端回落值保守得多，请用 `llm.context_window` 对齐服务端的 `num_ctx` / `-c` 设置。`/model` 的「API 拉取」探测失败时给出可读提示（如服务未启动时提示先运行 `ollama serve` / `llama-server`）。

不想把 API Key 明文写进配置文件时，`llm.api_key` 支持 pi 兼容的 `"!command"` 形式——配置加载时执行命令、取 stdout 首行作为密钥（适合接系统密钥管理器）：

```json
{ "llm": { "provider": "deepseek", "api_key": "!op read My-DeepSeek-Key" } }
```

如实边界：命令用 `shlex.split` 解析并**不经 shell 直接执行**，所以管道、环境变量展开、glob 等 shell 特性不可用，Windows 下路径要写正斜杠（反斜杠会被 shlex posix 模式吃掉）；单次解析有 10 秒超时，命令解析失败、可执行文件缺失、超时或输出为空都**降级为空 key**而不是报错——CLI 照常启动，Agent 随后给出正常的「未配置 LLM API key」提示，密钥本身不写日志。环境变量的优先级仍然更高：`NEXUSCLI_API_KEY` 或 provider 专属环境变量（如 `DEEPSEEK_API_KEY`）存在时会整体覆盖配置值，`!command` 只在环境变量未提供时生效。

两项默认关闭的可选诊断配置：

```json
{
  "telemetry": { "enabled": false },
  "llm": { "debug_dump": false }
}
```

- `telemetry.enabled`（默认 `false`）：为 `llm.chat` 与 `tool.call` 两条路径产出 OpenTelemetry span（属性带会话 trace_id）。未开启、或可选依赖 `opentelemetry` 未安装（`uv sync --extra telemetry`）时全链路 no-op，行为与没有遥测代码时完全一致
- `telemetry.otlp`（默认关闭）：把既有 span 经 OTLP/HTTP 导出到 Langfuse 等外部平台，需另装导出依赖（`uv sync --extra telemetry-otlp`）。三键：`endpoint`（端点完整 URL，**非空即开启**——空串是默认值，只配另外两键不开启任何行为；显式配置时 exporter 不会自动追加 `/v1/traces`，要写全，如 `https://cloud.langfuse.com/api/public/otel/v1/traces`）、`headers`（头名→值映射，随每个导出请求发送，Langfuse 鉴权即 `Authorization: Basic base64("公钥:私钥")`）、`timeout`（可选，单次导出请求超时秒数，缺省用 exporter 默认 10 秒）。仅在 `telemetry.enabled` 也为 `true` 时生效；导出是旁路——缺依赖、端点不可达或导出报错都只在进程内警告至多一次，CLI 功能与退出码零影响。Langfuse 端点示例与降级语义的完整口径见 [observability.md](observability.md)
- `llm.debug_dump`（默认 `false`）：开启后每回合把 LLM 请求与响应以 JSONL 追加写入 `~/.nexuscli/debug/llm/<日期>.jsonl`，`Authorization` 等敏感请求头会脱敏为 `<redacted>`；消息正文与工具定义会原样落盘，只在可信机器上临时开启排查
- `llm.vision`（默认不设置）：显式声明当前模型是否支持图片输入。不设置时按模型名特征猜测（名称含 vision/image/5v/vl）；自建网关经 `/model` 的「API 拉取」选择模型时会自动带入响应里声明的 `capabilities.vision`。名称不带特征但实际支持图片的模型，手动设 `"llm": { "vision": true }` 即可启用 `@image:` 流程

两者的完整口径（trace_id 关联、span 支持边界、落盘结构与用量落库覆盖面）见 [observability.md](observability.md)。

Agent 工具循环的回合上限由 `agent.max_turns` 控制（默认 200，加载时最小钳到 1）：单条消息内最多执行这么多轮工具调用；到达上限而模型仍想继续时，会输出显式 warning 提示任务可能未完成，长程任务可在 config.json 的 `"agent"` 段调大该值。子代理（`task` 工具委派）不继承该配置，而是使用独立的固定上限 `SUBAGENT_MAX_TURNS`（30），不随 `agent.max_turns` 放大。加载配置时若 `max_turns` 被判定为非数值（如字符串 `"abc"`），会回退默认 200，不会让 CLI 启动失败。

同段的 `agent.loop_detection_threshold`（默认 3，`0` 关闭，负数钳到 0，非数值同样回退默认）控制工具循环调用检测：同一工具以完全相同的参数连续重复第 N 次（N=阈值）时该次调用**不执行**，模型收到一条注入的换策略提示；若下一次仍是相同调用，则终止该回合并输出 loop-abort warning。因此 warning 有两种来源（`max_turns` 用尽与循环终止），语义相同——任务可能没有完成。指纹按「工具名 + 参数排序序列化」判定，参数有任何变化即重新计数。

同段的 `agent.prompt_profile`（默认 `"full"`，非法值回落 `"full"`）控制每次请求携带的工具 schema 规模：`"full"` 声明注册表里的全部工具；`"lean"` 只保留核心工具 schema（`read_file` / `write_file` / `edit_file` / `bash` / `list_dir` / `grep` / `glob` / `update_plan`，注册表里不存在的名字自然跳过），以压低固定 per-request 工具开销。它只收窄工具 schema，系统提示里的可用工具清单不变；默认行为不受影响。

## 固定 prompt/tool 开销诊断

固定开销（系统提示 + 工具 schema）在每条请求前都一样大，出问题时可以用两个口子直接看到：

- 单次 CLI 的 `--json` 在 `usage.prompt_breakdown` 输出 react 模式最后一次 LLM 请求的分解（CLI 在 llm client 边界测量，与 `Agent.last_prompt_breakdown` 同口径）：`system_chars` / `tools_chars` / `history_chars`（按消息正文计，图片列表与 assistant tool_calls 按其 JSON 长度计）、`tool_count`，以及按 chars/4 估算的 `estimated_tokens`（`ceil(总字符数 / 4)`——是诊断估算值，不是供应商口径，缓存与分词都会让真实 `input_tokens` 有出入）。plan/team 模式与未发起请求的运行保持旧契约，不添加该键。
- `nexuscli doctor --token-budget`（可配 `--cwd`）打印当前配置的基线：`prompt_profile`、`llm.context_window`、`llm.max_tokens` 与空 history 的固定开销 `baseline`（该目录真实注册表的 builtin + VFS + MCP 工具、按当前 profile 收窄后的工具 schema 与静态+动态系统提示）。lean 配置下 `tool_count` 与 `tools_chars` 相应下降，可与 full 对比看节省量。


## 模型、Token 与费用

默认 provider/model 是 `deepseek/deepseek-v4-flash`。DeepSeek V4 Flash/Pro 的内置 profile 使用 1M 上下文，并带有截至 2026-07-17 的官方每百万 Token 价格；价格会变化，因此可以用 `llm.context_window` 和 `llm.prices` 覆盖，未知 OpenAI-compatible 模型应显式配置。

`llm.prices` 的键是币种，值是每百万 Token 的 `{input_cache_hit, input_cache_miss, output}` 覆盖项（会与内置价格表合并、优先级更高）。本地 OpenAI-compatible 网关（如 Ollama、vLLM、LM Studio）的模型不在任何内置价格表里，不配置时 `/usage` 与 `/usage stats` 的成本恒为 0（token 计数正常）——想要成本估算就按你的计费方显式给出价格，例如：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "qwen2.5-coder",
    "prices": {
      "usd": { "input_cache_hit": 0.0, "input_cache_miss": 0.05, "output": 0.1 }
    }
  }
}
```

单次 CLI 的 `--json` 对未知价格显式标注：模型不在内置价格表且未配置 `llm.prices` 时，`cost` 为 `null`，并附 `cost_note: "unknown model price; configure llm.prices"`，`session_usage` 两个口径的 `cost_usd`/`cost_cny` 同样为 `null`（不再伪造 0.0）；token 计数不受影响。配置了价格（或命中内置表）的模型行为不变。示例如下：

```json
{
  "text": "...",
  "cost": null,
  "cost_note": "unknown model price; configure llm.prices",
  "session_usage": {
    "historical_total": { "total_tokens": 1234, "cost_usd": null, "cost_cny": null },
    "current_process": { "total_tokens": 1234, "cost_usd": null, "cost_cny": null }
  }
}
```

流式请求开启 `stream_options.include_usage`，并解析 `choices=[]` 的 usage-only 块、cache hit/miss 和 reasoning Token。REPL 用 `/usage` 查看最近一次普通 ReAct（含 `session_usage` 双口径，见 [repl-commands.md](repl-commands.md)），用 `/usage stats [N天]` 查看本地落库的近 N 天汇总，单次 CLI 用 `--json` 获取完整 usage/cost——带 `-c` / `--resume` 时 payload 还会附 `session_usage`（恢复基线 + 本次运行的历史累计，与仅本次运行的双口径；单次模式不把 usage 回写转录，该值为提示性数据）。成本以供应商返回的实际 Token 为准，不能只用"代码行数"精确推算。
