# 插件系统（本地目录 MVP）

> 本文是 [README](../README.md)「🧱 插件系统」章节的完整版。

插件是一个放在项目 `.nexuscli/plugins/<name>/` 下的目录：一个 `plugin.json` manifest 加最多三个组件子目录（实现：`src/nexuscli/plugins/loader.py`）。启动装配时（`bootstrap.py` 第一步）只做**发现**——纯文件读取，无网络、无执行；三类组件的并入发生在各自的注册表加载函数里。

## 目录布局

```text
.nexuscli/plugins/my-plugin/
├── plugin.json
├── agents/
│   └── reviewer.md          # 子代理定义（frontmatter: name/description/tools）
├── skills/
│   └── refactoring/
│       └── SKILL.md         # Skill 包（目录名即技能名）
└── commands/
    └── review.md            # 自定义斜杠命令
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
- 组件目录存在才并入（存在性判定，不要求三者齐备）

## 三类组件并入何处

| 组件目录 | 并入目标 | 优先级 |
|---|---|---|
| `agents/*.md` | `task` 工具可委派的子代理注册表（`agent/subagent.load_subagents`） | plugin > 项目 > 用户 > 内置 |
| `skills/*/SKILL.md` | Skill 注册表（`skill/registry.py`，在项目层之后扫描） | plugin > 项目 > 用户 > builtin |
| `commands/*.md` | 自定义斜杠命令查找（`entrypoints/slash_commands.load_slash_commands`） | plugin > 项目 > 用户 |

并入遵循各注册表既有的「后扫描者覆盖」链，**没有** `${plugin}:${component}` 命名空间隔离——插件组件与手写组件同名时直接覆盖。

## 健壮性约定

- `plugin.json` 缺失、不可读、非 UTF-8 字节、非法 JSON、非对象、缺 `name`：以 `[nexuscli:plugins]` 前缀发 `warnings.warn` 并**只跳过该插件**，其余插件与启动流程不受影响——一个坏插件不能拖垮 CLI 启动
- 发现结果按解析后的 cwd 做进程级缓存：坏 manifest 每进程只警告一次（启动装配预热缓存）

## 如实边界

- **仅项目级本地目录**：没有用户级插件目录（`~/.nexuscli/plugins/` 不生效），没有远程/市场分发（GitHub 归档、zip 均不支持）
- manifest 不支持显式组件路径声明（ZCode `collectComponentDirs` 的字符串/数组形式未移植）
- hooks / MCP servers / LSP servers / output styles 等其他组件类型不在本切片
- 无用户配置项（userConfig）、无诊断结构（PluginDiagnostic）
