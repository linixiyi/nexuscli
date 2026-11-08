# 插件系统（本地目录 + 工具组件）

> 本文是 [README](../README.md) 深入文档索引「插件系统」条目的完整版。

插件是一个放在**项目** `.nexuscli/plugins/<name>/` 或**用户级** `~/.nexuscli/plugins/<name>/` 下的目录：一个 `plugin.json` manifest 加最多四个组件子目录（实现：`src/nexuscli/plugins/loader.py`）。启动装配时（`bootstrap.py` 第一步）只做**发现**——纯文件读取，无网络、无代码执行；三类提示式组件的并入发生在各自的注册表加载函数里，tool 组件经 `register_plugin_tools` 在注册阶段并入 `ToolRegistry`。

## 目录布局

```text
.nexuscli/plugins/my-plugin/          # 项目级；~/.nexuscli/plugins/my-plugin/ 同构
├── plugin.json
├── agents/
│   └── reviewer.md          # 子代理定义（frontmatter: name/description/tools）
├── skills/
│   └── refactoring/
│       └── SKILL.md         # Skill 包（目录名即技能名）
├── commands/
│   └── review.md            # 自定义斜杠命令
└── tools/
    └── echo.json            # 工具组件声明（见下）
```

## plugin.json

```json
{
  "name": "my-plugin",
  "description": "团队共享的评审代理与命令",
  "version": "0.1.0"
}
```

- `name` 必填（非空字符串）；`description` / `version` 可选，非字符串值回退默认而不是拒绝整个插件；未知键静默忽略
- 组件目录存在才并入（存在性判定，不要求四者齐备）

## 两级插件目录与同名冲突

- 项目级 `<cwd>/.nexuscli/plugins/` 与用户级 `~/.nexuscli/plugins/` 都会被扫描
- **同名插件项目级胜出**：`load_plugins` 每个插件名最多返回一份，两级别都有 `acme` 时取项目级目录——与既有「项目覆盖用户」的方向一致
- 发现结果按 (解析后 cwd, 解析后 home) 做进程级缓存：坏 manifest 每进程只警告一次

## 组件命名空间：`<plugin>.<component>`

插件组件的引用名为 `<plugin>.<component>`（`nexuscli.plugins.component_ref`）。tool 组件注册进 `ToolRegistry` 时一律带命名空间前缀：插件 `acme` 的 `echo` 工具注册为 `acme.echo`——两个插件可以各带一个同名工具互不冲突，内置工具也不可能被插件遮蔽（内置名不含 `.`）。

agents / skills / commands 三类提示式组件暂沿用各注册表既有的「后扫描者覆盖」链（见下表），命名空间隔离目前只在 tool 组件上生效。

## 三类提示式组件并入何处

| 组件目录 | 并入目标 | 优先级 |
|---|---|---|
| `agents/*.md` | `task` 工具可委派的子代理注册表（`agent/subagent.load_subagents`） | plugin > 项目 > 用户 > 内置 |
| `skills/*/SKILL.md` | Skill 注册表（`skill/registry.py`，在项目层之后扫描） | plugin > 项目 > 用户 > builtin |
| `commands/*.md` | 自定义斜杠命令查找（`entrypoints/slash_commands.load_slash_commands`） | plugin > 项目 > 用户 |

## 工具组件（tools/）

`tools/<tool>.json` 每份声明一个工具，四个字段都必填：

```json
{
  "name": "echo",
  "description": "把输入原样返回",
  "parameters": {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"]
  },
  "handler": "handler.py:run"
}
```

- `name` 只允许 `[A-Za-z0-9_-]`（最终注册名为 `<plugin>.<name>`）；`parameters` 是 JSON Schema 对象，`required` 会同步为工具入参校验
- `handler` 是 `"文件.py:属性"` 形式的引用，相对插件根目录解析；**指向插件根之外的路径（含 `../` 逃逸与绝对路径）在注册时被拒绝**——插件的代码只能来自插件目录内
- handler 是同步或异步的 `(payload, context) -> 结果` 调用：返回 `ToolResult` 原样采用，其他返回值转字符串作为 `content`——常见 handler 无需 import nexusCLI

### 注册与审批

- 发现阶段只读 manifest 与声明文件（纯数据），**不执行任何插件代码**；handler 模块只在 `nexuscli.plugins.register_plugin_tools(registry, cwd)` 注册时导入一次（按解析路径缓存于 `sys.modules`，重复注册不会重复执行模块体）
- 注册后的插件工具与内置工具走**同一个 `ToolExecutor` 审批链**：permission 规则 → hooks → HITL 审批 → 审计，一条不少
- 插件工具按最保守默认构造：`is_read_only=False`（plan 模式拦截）、`is_concurrency_safe=False`、`danger_level="medium"`、**`requires_approval=True`——项目级插件默认需要审批**（插件随仓库而来，代码作者不可信；显式 `permissions.allow: ["acme.echo"]` 规则是预先豁免的通道）
- 一次坏导入只跳过自己的工具：handler 缺失、导入抛错、属性不可调用都以 `[nexuscli:plugins]` 告警并跳过该工具，其余插件照常注册

## 健壮性约定

- `plugin.json` 缺失、不可读、非 UTF-8 字节、非法 JSON、非对象、缺 `name`：以 `[nexuscli:plugins]` 前缀发 `warnings.warn` 并**只跳过该插件**，其余插件与启动流程不受影响——一个坏插件不能拖垮 CLI 启动
- `tools/*.json` 声明逐份校验，坏声明只跳过该声明（每份各发一次告警），同名声明保留第一份
- 启动装配预热发现缓存，随后各消费者不再重扫、不再重复警告

## 如实边界

- 仅本地目录：没有远程/市场分发（GitHub 归档、zip 均不支持）
- manifest 不支持显式组件路径声明；hooks / MCP servers / LSP servers / output styles 等其他组件类型不在本切片
- 无用户配置项（userConfig）、无诊断结构（PluginDiagnostic）
- `<plugin>.<component>` 命名空间目前只在 tool 组件注册上生效；agents/skills/commands 仍按「后扫描者覆盖」链合并
- `register_plugin_tools` 是工具组件的注册入口；`bootstrap.build_tool_registry` 在启动装配时调用它（内置工具与 vfs 之后、MCP 之前），因此 REPL / `-p` / runtime 的生产工具表都包含 `<plugin>.<tool>`——发现失败走 `[nexuscli:plugins]` 告警，坏 handler 只跳过自己的工具；项目级插件工具保持 `requires_approval=True`，与内置工具走同一 ToolExecutor 审批链
