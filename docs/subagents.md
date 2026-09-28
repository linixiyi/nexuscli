# 子代理 task 工具

> 本文是 [README](../README.md)「🧩 子代理 task 工具」章节的完整版。

`task` 工具把一个自包含任务委派给独立的子代理：子代理有自己的历史、Skill 缓冲和工具集，跑完后只把最终报告交回主会话。模型侧的调用形如 `task(description="调研压缩实现", prompt="...", agent_type="explore")`。

内置两个代理，始终可用：

- `general-purpose`：通用任务求解，适合研究、多步实现与代码分析（默认值）
- `explore`：只读代码探索，工具集固定为 `read_file`、`glob_files`、`grep`、`search_code` 等检索类白名单，绝不修改任何内容

自定义代理放进 `~/.nexuscli/agents/*.md`（用户级）或 `.nexuscli/agents/*.md`（项目级，同名覆盖），frontmatter 用简单的 `key: value` 逐行写法，正文即系统提示：

```markdown
---
name: reviewer
description: 只读代码评审代理，输出带文件行号的问题清单
tools: read_file, grep, glob_files, search_code
---
你是严格的代码评审代理。只读代码，不做任何修改，
按严重程度输出问题清单，每条附绝对路径与行号。
```

- `name` 与 `description` 必填，缺一则跳过该文件；`tools` 为逗号分隔白名单，省略则可用全部内置工具；`model` 目前解析但忽略（预留字段）
- 深度限制为 1：子代理不能再委派子代理，且 `task` 工具默认从子代理工具集中移除，双保险防递归
- 权限规则、HITL 审批回调原样透传给子代理；plan 态下 `task` 与其他非只读工具一样被硬拒绝
