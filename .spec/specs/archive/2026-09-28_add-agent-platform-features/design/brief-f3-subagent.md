# F3 子代理 task 工具 — 实现简报（第二波）

必读：`design/refs-notes.md` §3；`src/nexuscli/tools/base.py`（Tool/ToolContext/object_schema）；`src/nexuscli/tools/builtins.py`（注册模板：`Tool(name=..., parameters=object_schema(...), handler=..., required_keys=...)`，handler 是 `async def _x(payload, context) -> ToolResult`）；`src/nexuscli/agent/agent.py`（Agent 构造参数与 `_run_react`；ToolContext 在 :181-186 构造）；`src/nexuscli/tools/registry.py`。

## 设计规格

### 1. `src/nexuscli/agent/subagent.py`（新建）
- 内置代理（常量字典）：
  - `general-purpose`：description="通用任务求解代理，适合研究、多步实现与代码分析"；系统提示要点：你是被主代理委派的独立代理；一次性完成指定任务；产出一份自包含的最终报告（结论、关键文件路径、验证结果）；不要反问用户。
  - `explore`：description="只读代码探索代理，擅长检索与定位，不做任何修改"；系统提示要点参照 opencode explore（绝不写文件、绝不运行修改系统状态的命令、返回绝对路径、高效检索后给出结构化发现）；工具集固定白名单：`read_file, list_dir, glob_files, grep, directory_tree, get_file_info, search_code, web_search, web_fetch`。
- `@dataclass class AgentDefinition: name, description, prompt(系统提示), tools: list[str] | None`（None=全部可用）。
- `load_subagents(cwd) -> dict[str, AgentDefinition]`：读 `<cwd>/.nexuscli/agents/*.md` 与 `~/.nexuscli/agents/*.md`（user 先、project 后同名覆盖）。frontmatter 解析与 `entrypoints/slash_commands.py` 同款简单 `key: value` 逐行解析（不引 YAML 库）：`name`（必填，缺则跳过该文件）、`description`（必填）、`tools`（逗号分隔，可省略）、`model`（**解析但忽略**——预留字段）。正文为系统提示。内置两个代理始终存在，自定义同名覆盖内置。
- `async def run_subagent(agent_def, prompt, context: ToolContext) -> str`：
  1. 深度防护：`context.subagent_depth >= 1` → raise ValueError("Subagent depth limit reached (1) …")（捕获后返回错误 ToolResult 给模型）。
  2. 过滤注册表：新 `ToolRegistry()`，基于 `get_builtin_tools()`，`agent_def.tools` 非 None 时只注册白名单内工具名。
  3. 构造子 Agent：`llm_client=create_llm_client(context.config.llm)`、`config=context.config`；`system_prompt` = 代理提示（不用主会话 PromptAssembler）；独立 `history=[]`、独立 `SkillContextBuffer()`；`approval_callback` 透传 `context.approval_callback`（权限规则/审批同等生效，spec §7 风险缓解）。
  4. 子 ToolContext：`subagent_depth=context.subagent_depth + 1`，其余句柄透传。
  5. 消费 `agent.run(prompt)` 事件流：收集最后一条 assistant 文本（收 `text_delta` 累积、遇 `tool_call` 清空缓冲），流结束取最终文本；空则返回 "(subagent returned no text)"。
- 递归防护双保险：子注册表若含 task 工具且白名单未显式允许，也移除（参照 opencode 默认 deny task）。

### 2. `tools/base.py` ToolContext 只加一个字段
```python
subagent_depth: int = 0
```
（**勿动 `agent/agent.py`**——那是并行车道 F2 的领地；也**不要**给 ToolContext 加 llm_client/registry_handle 之类句柄字段，主 Agent 不会帮你填。）

- LLM client：`run_subagent` 内 `from nexuscli.llm.factory import create_llm_client` 用 `create_llm_client(context.config.llm)` 新建（同配置新实例，httpx client 无状态，与"复用主 Agent 的 config"语义等价——spec 2.2 的"复用 client"按此落地）。
- 子注册表：**只基于 `get_builtin_tools()` 过滤构建**（不含 MCP 工具——spec verify 清单不要求，注明局限即可），新 `ToolRegistry()` + `register_all`。
- 审批回调透传 `context.approval_callback`；cwd/config 用 `context.cwd`/`context.config`；SkillContextBuffer 全新实例。

### 3. `tools/builtins.py` 注册 `task` 工具
- name="task"；`is_read_only=False`（其子代理可能写文件，进串行道）、`is_concurrency_safe=False`、`requires_approval=False`、`danger_level="medium"`。
- parameters：`description`（3-5 词任务简述）、`prompt`（完整任务指令）、`agent_type`（默认 "general-purpose"）。required_keys=["description", "prompt"]。
- handler：解析 agent_type → 不存在时返回错误 ToolResult（内容含可用类型列表，给模型自我纠正）；调 `run_subagent`；结果 ToolResult 内容为子代理最终报告（可加一行前缀 `Subagent report (agent_type):`）。

### 4. 测试 `tests/test_subagent.py`（fake LLM：手写最小 stub 实现	async chat 流，参考既有测试里对 llm_client 的 fake 方式；不联网）
覆盖：自定义代理 frontmatter 解析（name/description/tools/model 忽略）；无效文件（缺 name）跳过；explore 工具集过滤（子注册表不含 write_file/bash）；子代理独立 history（跑完后主 agent.history 不增长）；深度 1 防递归（subagent_depth=1 的 context 再调 task → 错误结果）；agent_type 不存在 → 错误结果含可用列表；正常路径返回最终报告文本；审批回调透传（子代理内 requires_approval 工具触发 fake callback）。
