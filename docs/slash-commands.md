# 自定义斜杠命令

> 本文是 [README](../README.md)「🪄 自定义斜杠命令」章节的完整版。

把 markdown 文件放进命令目录，文件名（去掉 `.md`）就是命令名：

- 用户级：`~/.nexuscli/commands/<命令名>.md`（跨项目可用）
- 项目级：`.nexuscli/commands/<命令名>.md`（同名时覆盖用户级；该目录默认被 gitignore，适合放个人常用命令）

文件格式（frontmatter 全部可选）：

```markdown
---
description: 对指定代码做快速 review
mode: plan
allowed-tools: read_file, grep, glob_files
argument-hint: <文件路径>
---
请对下面的目标做 code review：

$ARGUMENTS
```

- `$ARGUMENTS` 会被替换为命令后面的参数；`$1`..`$9` 依次接收前九个位置参数（按空白切分、支持引号，越界替换为空串）；没有任何占位符时，参数会追加到提示词末尾
- frontmatter 的 `description` 会显示在 `/help` 里，`argument-hint` 提示参数写法
- `mode: react|plan|team` 让命令在指定模式下运行（如 `plan` 只读审阅），运行完恢复原模式
- `allowed-tools` 逗号分隔，命令运行期间只保留白名单内的工具，结束后恢复
- 正文支持 `` !`cmd` `` 注入：展开时先执行命令并把 stdout 替换进提示词（30 秒超时）。被命令守卫判为高危的命令会被拒绝，原片段替换为 `[refused: <cmd> — blocked by command guard]` 标记，不会静默执行也不会炸掉整个展开
- REPL 输入 `/命令名` 或单次模式 `nexuscli -p "/命令名 参数"` 都会展开为提示词发给模型
- 示例见仓库 `examples/commands/`，复制到命令目录即可使用

注意：在 Git Bash 里调用 `-p "/命令名"` 时，MSYS 可能把开头的 `/` 当路径转换；加 `MSYS_NO_PATHCONV=1` 前缀即可。交互模式不受影响。
