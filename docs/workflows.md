# 动态工作流与 /expert

> 本文是 [README](../README.md)「🔀 动态工作流与 /expert」章节的完整版。

NexusCLI 提供一个 JSON 步骤图引擎（实现：`src/nexuscli/workflow/graph.py`，纯 schema 层，无网络无执行）和 REPL 侧的依赖批次并行派发器（`workflow_cmds._run_workflow_graph`）。两个入口共用同一引擎与提示词层：

- `/workflow run <file>.json`：加载你写的步骤图（路径相对当前工作区解析）；
- `/expert <topic>`：内置固定三步专家流程，无需写 JSON。

## 工作流文件格式

```json
{
  "steps": [
    { "id": "research",  "prompt": "只读调研 X 的现状、相关文件与约束，输出要点清单", "depends_on": [] },
    { "id": "implement", "prompt": "基于调研结论完成 X 的最小改动实现", "depends_on": ["research"] },
    { "id": "verify",    "prompt": "验证 X 的实现，对照目标指出缺口与风险", "depends_on": ["implement"] }
  ]
}
```

每个 step 三个字段：`id`（非空字符串，唯一）、`prompt`（非空字符串，派发给子代理的完整指令）、`depends_on`（字符串数组，缺省 `[]`）。顶层是 `{"steps": [...]}`，未知顶层键被忽略。

## 校验规则（全部拒绝即报 `Workflow error`）

- 顶层必须是 JSON 对象；`steps` 必须是非空列表，每个 step 必须是对象
- `id` / `prompt` 必须是非空字符串；`id` 重复拒绝
- `depends_on` 必须是字符串数组；引用不存在的 id（含自依赖）拒绝
- 依赖图含环（1/2/3 节点各种形态）在加载时即被 DFS 检出并拒绝
- 文件不可读、非 UTF-8、JSON 语法错误同样报 `Workflow error`，不会让 REPL 退出

## 执行语义

- **依赖批次并行派发**：每轮取全部依赖已完成的 ready 步骤，按拓扑位序取前 `workflow.max_parallel_steps` 个（默认 3，`1` 严格退化为旧的串行行为）用 `asyncio.gather` 并发派发（实现：`workflow_cmds._run_workflow_graph`）。某一批次内任一步骤失败时，已在跑的兄弟步骤会等它自然跑完（不取消、报告照常回收），但不再派发后续批次——失败步骤之后未运行的步骤会列出并中止（它们的上游输入已缺失）。
- **每步都是 `general-purpose` 子代理**：步骤不能声明代理类型；子代理收到的是受控编排提示词——先列出全图拓扑序，再指定「本次执行的步骤 k/n」，原样注入上游报告，最后要求输出自包含步骤报告。
- **每步独立邮箱**：并发派发时同名兄弟步骤若共用一个邮箱收件箱，会互相消费对方的消息（mailbox 是读后清空的 drain 语义）。因此每个步骤都被派发为独立的邮箱代理名 `general-purpose.<步骤 id 清洗后>`（收件箱按代理名分文件，派发前会清掉同会话内早前工作流运行残留的旧收件箱），并发批次内兄弟步骤的邮箱名会写进各自的派发提示词，跨步骤同步用 `mailbox_post` 的 `to` 指定对方的邮箱名即可。
- **上游报告注入**：步骤 prompt 会带上所有 `depends_on` 上游的完整报告原文。
- **失败即中止**：某步抛错时打印 `[workflow] Step <id> failed: ...` 并中止剩余步骤，已完成的报告不回滚。
- **进度与汇收**：派发时打印 `[workflow] (k/n) dispatching step <id>`；全部完成后打印 `Workflow complete — n step reports:` 并逐一输出 `=== <step_id> ===` 报告。

并发上界在配置里调（`~/.nexuscli/config.json` 或项目 `.nexuscli/config.json`）：

```json
{ "workflow": { "max_parallel_steps": 2 } }
```

## /expert <topic>

`/expert` 把主题代入固定的三步线性链（实现：`slash_commands._EXPERT_STEPS`）：

1. `research` — 围绕主题只读调研：现状、相关文件与约束，输出带依据的要点清单
2. `execute` — 基于调研结论做最小改动实施，过程中做只读验证
3. `report` — 复核成果、指出缺口与风险，输出最终综合报告

ZCode 参考实现的八阶段专家工作流与 critic 循环**未**移植，这里就是这三步。

## 如实边界

- **usage 覆盖面**：工作流/专家派发的每一步，只要真实产出了 token，就由 `subagent.run_subagent` 的 done 事件记录器以 `source=subagent` 落一行进 `~/.nexuscli/usage.db`（`/usage stats` 的 By source 表可见）；零 token 的空跑（如脚本化 stub）不落行。派发器本身不再写任何行，工作流回合也不注入会话目标提醒（与 `/init`、`/plan` 同一边界）
- **`workflow.max_parallel_steps`**：默认 3；配置为 0/负数会被钳到 1，非数值回退默认。它是并发的上界而不是承诺——每批实际并发数受当前 ready 步骤数限制；`1` 即严格串行
- 步骤报告只打印到控制台，不做产物持久化与快照存储；没有断点续跑/暂停语义
- 与 `/init` 一致的优先级约定：用户/项目自定义命令若叫 `workflow` 或 `expert`，会遮蔽内置分支
- ZCode 的 critic 迭代、澄清轮次、策略配置均未移植（并发调度前沿已按上面语义实现）
