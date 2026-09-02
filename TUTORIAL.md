# NexusCLI 运行教程

本教程带你从零把 NexusCLI 跑起来：安装依赖 → 配置模型 API → 验证环境 → 真正跑通第一次 Agent 任务。配置示例以 OpenCodeGO 中转站（OpenAI-compatible / Chat Completions 格式）为准，同样适用于 DeepSeek、GLM 等官方直连。

> 所有命令默认在项目根目录 `NexusCLI/`（即本仓库）下执行，终端用 PowerShell、CMD 或 Git Bash 均可。

---

## 1. 前置条件

| 依赖 | 要求 | 说明 | 检查命令 |
| --- | --- | --- | --- |
| Python | 3.11 或更新 | 运行时 | `python --version` |
| [uv](https://docs.astral.sh/uv/) | 任意近期版本 | 包管理 + 运行入口，**必装** | `uv --version` |
| rg (ripgrep) | 可选 | 更快的本地代码搜索 | `rg --version` |
| Node.js | 20.19+（可选） | 仅 Chrome DevTools MCP 需要 | `node --version` |

没装 uv 的话，Windows PowerShell 一行安装：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 2. 安装依赖

```bash
uv sync --extra dev
```

uv 会自动创建虚拟环境（`.venv/`）、按 `pyproject.toml` + `uv.lock` 安装精确版本的依赖，并把本项目以可编辑方式装进去。之后所有命令都用 `uv run nexuscli ...` 的形式执行，不需要手动激活虚拟环境。

首次验证安装成功：

```bash
uv run nexuscli --version
# 输出: nexuscli 0.1.0

uv run nexuscli --help
```

## 3. 配置模型 API（核心步骤）

NexusCLI 需要一个 OpenAI-compatible 的 Chat Completions 接口。下面按中转站（如 OpenCodeGO）的配置来写，**推荐用项目根目录的 `.env` 文件**，这是最省事、最不容易漏的方式。

### 3.1 创建 `.env` 文件

在项目根目录新建名为 `.env` 的文件（没有扩展名前缀的点），内容如下：

```dotenv
NEXUSCLI_PROVIDER=openai-compatible
NEXUSCLI_BASE_URL=https://<你的中转站地址>/v1
NEXUSCLI_MODEL=deepseek-v4-flash
NEXUSCLI_CONTEXT_WINDOW=1000000
NEXUSCLI_API_KEY=sk-你的APIKey
```

逐行解释（对应服务商后台截图里的字段）：

| 环境变量 | 填什么 | 对应后台字段 | 注意事项 |
| --- | --- | --- | --- |
| `NEXUSCLI_PROVIDER` | `openai-compatible` | API 格式 = Chat Completions | 凡是兼容 `/chat/completions` 的中转/自建服务都用这个 |
| `NEXUSCLI_BASE_URL` | `https://<你的中转站地址>/v1` | Base URL | **填到 `/v1` 为止**。程序会自动在后面拼 `/chat/completions`，不要自己把 `/chat/completions` 写进去，也不要少写 `/v1` |
| `NEXUSCLI_MODEL` | `deepseek-v4-flash` | 模型列表 | 必须和服务商模型名**一字不差** |
| `NEXUSCLI_CONTEXT_WINDOW` | `1000000` | 模型徽标（如 1M） | 自定义模型默认按 128K 处理，按后台标注填可以充分利用长上下文 |
| `NEXUSCLI_API_KEY` | `sk-...` | API Key | 服务商签发的密钥 |

> **安全提醒**：`.env` 已在 `.gitignore` 里，不会被提交到 git。不要把真实 Key 写进 README、教程或任何会被提交的文件，也不要发给别人；Key 泄漏后请立即在服务商后台重置。

### 3.2 其他配置方式（可选）

除 `.env` 外，NexusCLI 还支持（优先级从低到高）：

1. 内置默认值
2. 用户级 `~/.nexuscli/config.json`（全局生效，Windows 即 `C:\Users\你\.nexuscli\config.json`）
3. 项目级 `.nexuscli/config.json`（只对当前仓库生效）
4. 项目级 `.env`（本教程推荐）
5. CLI 参数（临时覆盖，优先级最高）
6. 当前进程环境变量

JSON 配置文件对应写法（`~/.nexuscli/config.json`）：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "base_url": "https://<你的中转站地址>/v1",
    "model": "deepseek-v4-flash",
    "context_window": 1000000,
    "api_key": "sk-你的APIKey"
  }
}
```

临时用 CLI 参数覆盖（不改任何文件）：

```bash
uv run nexuscli --provider openai-compatible \
  --base-url https://<你的中转站地址>/v1 \
  --model deepseek-v4-flash \
  --api-key sk-你的APIKey \
  -p "你好"
```

如果用 DeepSeek 官方直连，则更简单——不用填 base_url：

```dotenv
NEXUSCLI_PROVIDER=deepseek
NEXUSCLI_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=sk-你的DeepSeekKey
```

## 4. 检查环境

```bash
uv run nexuscli doctor --cwd .
```

关键字段确认这三项即可：

```json
{
  "api_key": "configured",
  "provider": "openai-compatible",
  "model": "deepseek-v4-flash"
}
```

- `api_key` 显示 `configured` 说明 Key 已被读取；显示 `missing` 说明 `.env` 没被找到或变量名拼错（见第 7 节排查）。
- `python`、`uv` 显示正常即可；`node`/`npx`/`rg` 是可选项，缺失只影响对应可选功能。

## 5. 运行

### 5.1 单次任务（推荐第一次先跑这个）

```bash
uv run nexuscli --plain -p "请用一句话介绍你自己,然后读取 README.md 的第一行并告诉我内容"
```

实际运行输出示例：

```text
我是 NexusCLI，一个运行在终端中的 Python AI Agent CLI，对标 Claude Code，提供交互式 REPL、单次 Prompt、SDK、MCP Server 和后台 Task Worker 等能力。

现在读取 README.md 的第一行：README.md 的第一行内容是：`# NexusCLI`
```

看到模型自我介绍、并且**自动调用了 `read_file` 工具**读到真实文件内容，说明整条链路（流式调用 → ReAct 工具循环 → 结果回灌）已经完全跑通。

想看本次任务的 Token 用量，加 `--json`：

```bash
uv run nexuscli -p "只回复两个字:成功" --json
```

```json
{"text": "成功", "mode": "react", "turns": 1,
 "usage": {"input_tokens": 29463, "output_tokens": 62, "total_tokens": 29525}}
```

> `cost` 字段为空是正常的：自定义 openai-compatible 模型没有内置单价，需要时可在配置的 `llm.prices` 里填每百万 Token 单价。

### 5.2 交互模式（REPL）

```bash
uv run nexuscli
```

进入后直接打字对话即可，Agent 会自主调用工具。常用操作：

- 输入 `/help` 查看全部命令，`/exit` 或 `Ctrl+C` 退出
- `/tools` 查看可用工具，`/config` 查看运行时配置，`/usage` 查看上次用量
- `/model` 打开交互式模型选择器（可保存多套 BYOK 配置到 `~/.nexuscli/models.json`）
- `Shift+Tab` 切换会话权限模式：`Default`（危险操作需人工确认）↔ `Auto (full access)`（免审批，谨慎使用）

常用斜杠命令速查：

| 命令 | 作用 |
| --- | --- |
| `/plan <任务>` | 先规划成 DAG 再按依赖执行 |
| `/team <任务>` | 多 Agent 协作（Planner/Worker/Reviewer） |
| `/save <事实>` | 写入长期记忆 |
| `/memory [search\|stats]` | 查看/搜索长期记忆 |
| `/index [路径]` | 建本地代码索引，配合 `/search <query>` |
| `/snapshot` / `/restore <id>` | 项目快照与恢复现场 |
| `/resume` / `/resume <序号\|id>` | 查看/切换历史会话 |
| `/skill list` | 查看已装的 Skill |
| `/clear` | 清空当前会话上下文（并开启新会话） |

**会话会自动保存**：REPL 里的每轮对话都写入 `~/.nexuscli/sessions/`，退出后可以继续：

```bash
uv run nexuscli sessions            # 列出本项目的会话
uv run nexuscli -c                  # 继续最近一次会话
uv run nexuscli --resume <id>       # 按会话 id 恢复
```

单次模式同样能带历史：`uv run nexuscli -p "继续刚才的任务" -c`。

**自定义命令（可选）**：把 markdown 提示词文件放进 `~/.nexuscli/commands/`（用户级）或
`.nexuscli/commands/`（项目级），文件名就是命令名，REPL 和 `-p` 模式都能用：
`/命令名 参数` 会展开成提示词发给 Agent。示例见仓库 `examples/commands/`。

### 5.3 其他运行模式

```bash
# Plan-and-Execute 模式
uv run nexuscli --mode plan -p "先读取 README，再验证项目"

# 多 Agent 协作模式
uv run nexuscli --mode team --worker-mode plan -p "并行审计核心模块"
```

## 6. 把 Agent 用起来（下一步建议)

- 在项目里放一个 `NEXUS.md`（或 `AGENTS.md`）写清工程约定，Agent 每次运行都会读它作为长期上下文
- 用 `.nexuscli/skills/<名字>/SKILL.md` 沉淀项目级技能，`~/.nexuscli/skills/` 放跨项目技能
- 接入 MCP 工具：`uv run nexuscli mcp init-chrome --scope project` 一键生成 Chrome DevTools MCP 配置
- 把 NexusCLI 当 MCP server 用：`uv run nexuscli mcp serve --transport stdio`

## 7. 常见问题排查

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `doctor` 显示 `api_key: missing` | `.env` 不在项目根目录、文件名不对（要叫 `.env`）、或变量名拼错 | 核对文件位置和 `NEXUSCLI_API_KEY` 拼写 |
| 报 `LLM API key is not configured` | 同上 | 同上 |
| HTTP 401 / `invalid api key` | Key 错误、过期，或复制时带了空格 | 重新复制 Key，确认未过期 |
| HTTP 404 / `model not found` | 模型名与服务商不一致，或 base_url 少了 `/v1` | 按服务商模型列表逐字核对 `NEXUSCLI_MODEL`；确认 URL 到 `/v1` 结束 |
| `Could not connect to ...` | 网络不通、需要代理，或 base_url 域名错误 | 浏览器能打开该域名后重试；需要代理时给终端设置 `HTTPS_PROXY` |
| 报 `LLM context window is not configured` | 该模型没有内置档案且未配置窗口 | 设置 `NEXUSCLI_CONTEXT_WINDOW`（如 `1000000`） |
| 回复为空或卡住 | 中转站限流/超时 | 稍后重试，或调大配置 `llm.timeout` |

排查配置是否生效的最快方式永远是：

```bash
uv run nexuscli doctor --cwd .
```

它显示的就是"当前实际会生效"的 provider/model/key 状态。

## 8. 开发与测试（可选）

```bash
uv run python -m ruff check .        # 代码检查
uv run python -m ruff format .       # 格式化
uv run python -m pytest              # 单元测试
uv build                             # 构建发布包
```

---

配置或运行中遇到其他问题，先跑 `doctor`，再用第 7 节对号入座；仍然解决不了的话，带上 `doctor` 输出（Key 已自动脱敏）去排查。
