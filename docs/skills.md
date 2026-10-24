# Skill 匹配与沉淀

> 本文是 [README](../README.md) 深入文档索引「Skill 匹配与沉淀」条目的完整版。

Skill 按 `builtin -> user -> project` 加载，同名时后层覆盖前层：

- builtin：产品默认能力
- user：`~/.nexuscli/skills/*/SKILL.md`（原生格式，跨项目复用）；另扫描 Agent Skills 规范标准目录 `~/.agents/skills/`
- project：`.nexuscli/skills/*/SKILL.md`（原生格式，最贴近当前仓库）；另扫描规范标准目录 `.agents/skills/`

每层内部扫描顺序是标准目录在前、原生目录在后，所以同层同名时**原生 `.nexuscli/skills` 目录的 Skill 胜出**（规范目录的价值在于让生态里的标准 Skill 包原样加载，不改文件即可用）。

frontmatter 在 `name` / `description` / `tags` / `version` 之外还接受两个 Agent Skills 规范字段（连字符与下划线两种写法都认）：

- `allowed-tools`：逗号分隔字符串（规范写法，如 `Bash(git:*), Read`）或内联列表。`load_skill` 把 Skill 正文注入上下文时会附一行「该技能激活期间允许的工具」提示；**如实边界：这是提示性约束，执行器不做硬限制**，真正的工具白名单仍由代理 frontmatter 的 `tools` 控制。
- `disable-model-invocation`：`true` / `1` / `yes` 时该 Skill 退出自动 Top-K 匹配候选（模型不会被提示去加载它），但 `/skill load` 命令与显式 `load_skill` 调用照常可用——挡的是自动候选，不是加载通道。

每次用户输入先用 name、description、tags 做中英文词法/字符 n-gram Top-K 匹配，再把候选交给模型决定是否调用 `load_skill`。Skill 正文只在真正加载后进入当前 ReAct 的下一模型轮；每个并发子 Agent 都有独立 Skill 缓冲区，不会串线。

当一次成功流程具备稳定输入、明确步骤和可复用边界时，模型可以调用 `save_skill` 提议写入 project 或 user 层。该工具默认拒绝覆盖已有 Skill，并强制人工确认。
