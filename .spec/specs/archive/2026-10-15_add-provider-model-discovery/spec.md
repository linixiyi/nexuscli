# 本地网关 provider 模型发现与 /model 切换 - 项目范围

## 1. 问题定义
- **项目目标**：让 nexusCLI 用户给定一个 OpenAI-compatible API 地址后，`/model` 能自动拉取该地址 `/v1/models` 返回的可用模型列表并勾选切换，无需手工抄写模型 ID；同时把本机 `http://localhost:20128/v1` 本地网关配置成可直接运行。
- **目标用户**：使用 nexusCLI 且自带 API 网关/中转站（OpenAI Chat Completions 协议）的用户。
- **核心价值**：像截图中的商业产品一样，填地址 → 拉模型列表 → 勾选添加，一轮完成；本地网关密钥不落仓库。

## 2. 假设与待确认

### 2.1 已确认事实
- 本地网关 `http://localhost:20128/v1` 在线：`GET /v1/models` 返回 28 个模型（`cbcn/*`、`cbai/*` 前缀），`POST /v1/chat/completions` 对 `cbcn/glm-5.3-flash` 正常出流（含 reasoning_content）。
- `/v1/models` 响应为标准 OpenAI `{"object":"list","data":[{"id",...,"context_length","max_completion_tokens"}]}`，`context_length` 字段可用来填 context_window。
- nexusCLI 已有：OpenAICompatibleClient（httpx）、`/model` 交互选择器（prompt_toolkit，Default/Custom 两个 tab）、CustomModelStore（`~/.nexuscli/models.json`，0o600 原子写）、`_activate_model` 热切换、`load_config` 支持 `NEXUSCLI_*` env。
- `Agent._validate_config` 要求 api_key 非空、max_context_window 非零。
- 用户级 `~/.nexuscli/config.json` 当前不存在。

### 2.2 关键假设
- "OpenAI Chat Completions" 协议即项目现有 `openai-compatible` provider 语义，无需新增 provider 类型。
- API key 允许写入用户级 `~/.nexuscli/config.json`（用户机器上的私有配置，非仓库文件）；仓库源码/测试/文档一律不含真实密钥字面量（Mimosa 约束）。
- `/v1/models` 探测需要（可选）携带 Authorization 头；网关无鉴权时也能 200，两种都兼容。

### 2.3 待确认问题
- 无：端到端验证模型已按既有确认定为 `cbcn/glm-5.3-flash`，无需用户裁决

### 2.4 可选解释与取舍
- 当前选择：模型发现逻辑放进 `nexuscli/llm/openai_compatible.py`（模块级函数，httpx 同步 client）而不是新开 `discovery.py` 模块 -> 复用同站点的超时/异常习惯，改动面最小。
- 当前选择：`/model` 选择器加第三个 tab「API 拉取（Fetch）」，拉取结果临时并入 Custom tab 展示，勾选后写入 CustomModelStore 持久化 -> 与截图交互（列表+勾选+添加所选）对齐，且复用现有渲染/激活链路。
- 当前选择：本地网关通过用户级 `~/.nexuscli/config.json` 配置（provider=openai-compatible、base_url、model），api_key 用 `!` 前缀 `!type ...` 命令形式从外置文件读取 —— 复用 load_config 既有的 `_resolve_api_key_command`，密钥不进任何仓库文件或证据记录。

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 模型发现：`fetch_available_models(base_url, api_key, timeout)` 返回 `list[ModelListingProfile]`，解析 `/v1/models` 的 `data[].id`，context_window 取 `context_length` 字段（缺省 128_000），拉取失败返回可读错误信息（连接拒绝/超时/HTTP 4xx5xx/响应格式非 list）。
- [x] `/model` 选择器新增 Fetch tab：输入 base URL（回车确认，默认取当前 provider 的 base_url）→ 拉取 → 模型列表展示 → ↑↓/空格 勾选 → Enter「添加所选」→ 逐个写入 CustomModelStore 并提示数量；Esc 取消。拉取失败在选择器内联显示错误后可重试或退出。
- [x] 配置本地网关：用户级 `~/.nexuscli/config.json` 写入 llm.provider/base_url/model=cbcn/glm-5.3-flash/context_window=1000000，api_key 用 `!` 命令形式读外置文件；密钥本体只存放在 `~/.nexuscli/gateway.key`（用户目录，非仓库）。
- [x] 端到端验证：`nexuscli "..."` 非交互跑一轮真实网关对话；`/model` 拉取列表路径用脚本级 async 调用验证（fetch→profile 构造→create_llm_client→chat 单轮）。

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 不做 Anthropic 协议网关的模型发现（只做 OpenAI-compatible）。
- 不做模型能力（vision/tools/reasoning）识别与过滤。
- 不做按提供商目录的模型价格/配额查询。
- 不改 web_fetch SSRF 守卫等安全约束；模型发现只允许 http/https 且不新增对 SSRF 守卫的绕过（用户显式提供的 base_url 属于 BYOK 配置面）。

## 4. 最小实现路径
- 最简单可行方案：`openai_compatible.py` 加一个模块级同步函数 + `model_selector.py` 加 Fetch tab 状态与渲染 + `model_cmds.py` 串输入→拉取→写入→激活；`~/.nexuscli/config.json` 用户级配置一次。
- 暂不引入：新的 LLM provider 子类、异步并发拉取、模型列表缓存/自动刷新、GUI。
- 不做的抽象/配置化：不引入 `llm.discovery` 配置节；不做 base_url 白名单；不把拉取结果做成常驻目录文件（CustomModelStore 即持久化）。

## 5. 技术决策
- 技术栈：现有 Python 3.11 + httpx + prompt_toolkit + rich，零新依赖。
- 本轮允许改动：`src/nexuscli/llm/openai_compatible.py`（新增模块级函数）、`src/nexuscli/entrypoints/model_selector.py`（Fetch tab）、`src/nexuscli/entrypoints/commands/model_cmds.py`（拉取编排）、`tests/`（新增测试）、用户级 `~/.nexuscli/`（config.json + gateway.key，运行时配置非仓库文件）、`README.md`（配置节补一段 /model 拉取说明）。
- 本轮不应触碰：`web_fetch` 工具与 SSRF 守卫、`config.py` 的 load/apply_env 主链路、`factory.py` 的 provider 分派、其他 slash commands。
- Git integration branch：`spec/2026-10-15_add-provider-model-discovery`

### 5.4 编排策略
- route: review
- route_decision 输出：`{"route": "review", "score": 2, "lanes": ["API / 接口", "测试", "文档"]}`
- 受本机 Agent 工具故障约束（子代理生成报 reasoning-level-missing，见记忆 agent-tool-reasoning-level-broken），sidecar 通道不可用；按约束降级为 local 单线程执行，routing/acceptance/gates 全程留在主会话，不使用伪造并行。
- loop/workflow slot 路由：不触发（无收敛循环、无批量 fan-out 形状）。

### 5.1 验证策略
- 范围级别：package
- 变更对象：llm 模型发现函数、model_selector Fetch tab、model_cmds 编排、新测试、README 配置节
- 快速检查：`uv run pytest tests/test_model_discovery.py tests/test_model_selector.py -q` + `uv run ruff check src/nexuscli/llm/openai_compatible.py src/nexuscli/entrypoints/model_selector.py src/nexuscli/entrypoints/commands/model_cmds.py tests/test_model_discovery.py`
- 集成检查：`uv run pytest tests/test_model_selector.py tests/test_config.py -q`（selector/激活/config 相邻路径回归）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或 package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- 标准 A：`fetch_available_models("http://localhost:20128/v1", key)` 返回含 `cbcn/glm-5.3-flash` 的 28 条 profile -> verify: `uv run pytest tests/test_model_discovery.py -q`（本地网关 live 测试 + mock 响应测试）
- 标准 B：`/model` Fetch tab 能输入地址、拉取列表、勾选、写入 models.json 并激活 -> verify: 自动化测试覆盖状态机与编排函数 + 真实网关脚本级验证输出（fetch→activate→chat 一轮）
- 标准 C：`nexuscli "hello"` 在用户级配置下真实运行 -> verify: 密钥经 env 注入执行一次真实对话，输出含 assistant 回复
- 标准 D：仓库内无真实密钥字面量 -> verify: `git grep -l` 用密钥真实前缀探针（完整值不入文档，探针在验收时以环境变量方式携带）零匹配

## 7. 风险与约束
- 风险点：prompt_toolkit 交互式选择器难以在 CI 自动化 -> 缓解：状态机与编排函数拆成可独立测试的纯逻辑，应用层只做键绑定。
- 风险点：网关模型 ID 含 `/`（cbcn/glm-5.3-flash），`/model <provider> <model>` 参数解析会把斜杠切开 -> 缓解：Fetch tab 路径不走该解析，直接以完整 id 构造 profile。
- 风险点：密钥经 `!` 命令读取在 Windows 下 shlex 解析路径 -> 缓解：`!` 命令不依赖 shell；外置文件放 `~/.nexuscli/gateway.key`（用户目录），配置里只写文件读取命令，真实值不进 config.json。
