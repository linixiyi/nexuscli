# 模型发现透出可选推理强度 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：`/model` 的「API 拉取」在拉取模型列表时同时透出该模型的推理强度能力（是否支持 effort 档位、可否关闭思考），让用户在选择模型时就能看到强度信息，选中激活后 `/effort` 行为与网关能力一致。
- **目标用户**：使用 OpenAI-compatible 网关的 nexusCLI 用户。
- **核心价值**：模型能力可见——用户不必逐个试 `/effort` 才知道支不支持强度档位。

## 2. 假设与待确认

### 2.1 已确认事实
- 本地网关 `/v1/models` 每个模型带 `capabilities` 对象，强度相关字段：`reasoning`（是否推理模型）、`thinkingFormat`（openai/deepseek/kimi/minimax/hunyuan/zai）、`thinkingCanDisable`（能否关闭思考）、`thinkingEffortSupported`（是否支持 effort 档位）、`thinkingRange`（本轮均为 null）。
- 28 个模型中仅 `cbai/deepseek-v4-pro` 的 `thinkingEffortSupported=true`。
- 实测网关接受 `reasoning_effort` 字段（不报错），但 glm-5.3-flash 消费与否由网关决定（响应 reasoning_tokens=192，与不传时同量级，说明该模型未消费档位）。
- nexusCLI 既有：`ModelListingProfile`（model/context_window/base_url）、`_REASONING_EFFORT_PROVIDERS` 白名单、`/effort minimal|low|medium|high` 命令。

### 2.2 关键假设
- 「可选强度」以网关声明的 `thinkingEffortSupported` 为准；OpenAI 标准协议没有该字段，无 capabilities 的网关一律视为「未知」，不猜。
- `thinkingRange`（若非 null）形如 `["low","high"]` 的档位范围；本轮网关全为 null，解析按「可选档位列表」处理，异常形状降级为未知。

### 2.3 待确认问题
- 无：字段口径已按网关实测声明确定，`thinkingRange` 异常形状按降级处理，无需用户裁决

### 2.4 可选解释与取舍
- 当前选择：capability 解析放进既有 `_parse_models_payload`（同文件扩展），不新建模块 -> 改动面最小。
- 当前选择：`ModelListingProfile` 只加两个能力字段（`reasoning_effort_supported: bool|None`、`thinking_can_disable: bool|None`），不做完整 capability 映射 -> 未请求的字段（vision/pdf/audio 等）不透出，保持最小。
- 当前选择：Fetch tab 徽标用一行文本后缀（`· 强度 ✓` / `· 思考可关`），不做独立列 -> 选择器渲染宽度可控。

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] `_parse_models_payload` 解析 `capabilities.thinkingEffortSupported` / `capabilities.thinkingCanDisable`，透传到 `ModelListingProfile`（缺 capabilities 视为 None=未知，不报错）。
- [x] Fetch tab 渲染行尾徽标：effort 支持显示「强度 ✓」，不可关思考的模型不加「思考可关」徽标；未知（None）不显示徽标。
- [x] 选中激活链路：`_store_fetched_profiles` 把能力写入 ModelProfile.description（如「支持强度档位 (minimal/low/medium/high)」），激活后用户 `/effort` 有据可依。
- [x] 真实网关验证：live fetch 断言 28 模型、deepseek-v4-pro 的 effort_supported=True、glm-5.3-flash 的 thinking_can_disable=True。

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 不做 thinkingRange 档位动态下发到 `/effort` 白名单（`/effort` 仍四档固定）。
- 不做 `reasoning_effort` 请求字段随模型能力自动开关（仍按 provider 白名单）。
- 不透出 vision/tools/pdf 等其他 capability。
- 不改 Anthropic 路径。

## 4. 最小实现路径
- 最简单可行方案：`_ModelListing` 加 2 字段 + `_parse_models_payload` 读 2 键 + `ModelListingProfile` 加 2 字段 + `_render_fetch` 加徽标 + `_store_fetched_profiles` 写 description。
- 暂不引入：capability 通用映射层、strength 配置节、徽标样式配置。
- 不做的抽象/配置化：不做「能力」数据类；布尔三态（True/False/None=未知）直接用。

## 5. 技术决策
- 技术栈：既有 Python 3.11 + httpx + prompt_toolkit + rich，零新依赖。
- 本轮允许改动：`src/nexuscli/llm/openai_compatible.py`（解析+数据类）、`src/nexuscli/entrypoints/model_selector.py`（徽标渲染）、`src/nexuscli/entrypoints/commands/model_cmds.py`（description 写入）、`tests/test_model_discovery.py`（新断言）。
- 本轮不应触碰：`/effort` 命令实现、`_REASONING_EFFORT_PROVIDERS`、chat 主链路、factory。
- Git integration branch：`spec/2026-10-15_add-effort-capability-to-discovery`

### 5.4 编排策略
- route: local
- route_decision 输出：`{"route": "local", "basis": "package-structure", "score": 0}`
- 主会话单线程执行，routing/acceptance/gates 全程留在主会话。

### 5.1 验证策略
- 范围级别：package
- 变更对象：openai_compatible 解析、model_selector 渲染、model_cmds description、测试
- 快速检查：`uv run pytest tests/test_model_discovery.py -q` + `uv run ruff format --check src tests` + `uv run ruff check <改动文件>`
- 集成检查：适用外：本轮没有直接受影响的跨模块用户路径
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或 package 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] 标准 A：live fetch 28 模型，能力字段正确透出 -> verify: `uv run pytest tests/test_model_discovery.py -q`（含 live 断言）
- [x] 标准 B：Fetch tab 渲染含强度徽标 -> verify: render() 片段断言测试通过
- [x] 标准 C：选中模型的 description 携带强度说明 -> verify: `_store_fetched_profiles` 测试断言
- [x] 标准 D：CI 口径全过 -> verify: `uv run ruff format --check src tests` + `uv run ruff check <改动文件>`

## 7. 风险与约束
- 风险点：不同网关 capabilities 形状不一 -> 缓解：所有 capability 读取走「存在且类型对才取值」，缺省即未知，不抛错。
- 风险点：prompt_toolkit 渲染宽度 -> 缓解：徽标追加在既有 context 后缀同一行，短文本。
