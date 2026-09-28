# Agent 平台能力移植（hooks/子代理/权限规则/命令增强/compact/plan 模式） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`
- 如果一个任务没有验证方式，就不能开始
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`

## 阶段一：澄清与初始化
- [x] 固化问题定义、关键假设与非目标
  - boundary: 只更新 `.spec/specs/2026-09-28_add-agent-platform-features/spec.md`
  - verify: `spec.md` 已写明事实、假设、待确认问题、不在范围内内容和当前 integration branch（已核对：含 2.1-2.4、3.3、分支名）
- [x] 从主分支创建独立工作分支
  - boundary: 只执行 Git 分支检查/创建，不改业务文件
  - verify: 当前分支为从 `main`（基线 415cb24，与 github/main 同步）创建的 `spec/2026-09-28_add-agent-platform-features`（init_spec_package.py 已创建并切换）

## 阶段二：核心实现
- [x] F1 权限规则引擎（permissions allow/deny/ask）
  - id: task-permissions
  - boundary: `src/nexuscli/config.py`（新增 `PermissionsConfig` 数据类与解析）、`src/nexuscli/policy/permission_rules.py`（新建：规则解析与匹配）、`src/nexuscli/tools/executor.py`（审批链前插入规则评估）、`tests/test_permissions.py`（新建）；不改变 HITL 三模式既有语义与默认行为
  - verify: `uv run python -m pytest tests/test_permissions.py -q` 通过，覆盖：deny 拒绝且含原因、allow 跳过 HITL、ask 强制 HITL、`bash(git diff:*)`/`write_file(src/**)`/`web_fetch(domain:...)` 三类语法匹配、user+project 层合并、无规则时与旧行为一致（回归用例）、审计日志记录规则命中
- [x] F2 hooks 生命周期钩子系统
  - id: task-hooks
  - depends-on: task-permissions
  - boundary: `src/nexuscli/config.py`（`HooksConfig`）、`src/nexuscli/hooks/`（新建包：registry 解析/合并、runner 子进程执行与 JSON 协议）、`src/nexuscli/tools/executor.py`（PreToolUse/PostToolUse 接入）、`src/nexuscli/agent/agent.py` 与 `src/nexuscli/entrypoints/repl.py`（UserPromptSubmit/Stop/SessionStart 触发点）、`tests/test_hooks.py`（新建）
  - verify: `uv run python -m pytest tests/test_hooks.py -q` 通过，覆盖：matcher 匹配（精确/正则/*）、exit 2 阻断工具调用、stdout JSON decision（deny+reason）生效、PreToolUse 的 allow 不绕过 requires_approval、timeout 生效、stdin 载荷含 tool_name/tool_input/session_id、未配置时零开销（回归用例）
- [x] F3 子代理 task 工具
  - id: task-subagent
  - depends-on: task-hooks
  - boundary: `src/nexuscli/tools/builtins.py`（`task` 工具注册）、`src/nexuscli/agent/subagent.py`（新建：代理定义加载 `.nexuscli/agents/*.md` 与 `~/.nexuscli/agents/*.md`、内置 general-purpose/explore、子代理运行器）、`src/nexuscli/tools/base.py`（ToolContext 增加子代理运行所需句柄）、`tests/test_subagent.py`（新建，fake LLM）
  - verify: `uv run python -m pytest tests/test_subagent.py -q` 通过，覆盖：自定义代理 frontmatter 解析（name/description/tools）、工具集过滤（explore 只读）、独立 history 不污染主会话、深度 1 防递归、返回最终报告文本、agent_type 不存在时报错给模型
- [x] F4 slash 命令增强（frontmatter/位置参数/shell 注入）
  - id: task-slash-commands
  - boundary: `src/nexuscli/entrypoints/slash_commands.py`（frontmatter 扩展、`$1..$9`、`` !`cmd` `` 展开）、`src/nexuscli/entrypoints/repl.py`（mode/allowed-tools 应用）、`tests/test_slash_commands.py`（新建或并入既有命令测试）
  - verify: `uv run python -m pytest tests/test_slash_commands.py -q` 通过，覆盖：`mode`/`allowed-tools`/`argument-hint` 解析、位置参数替换与 `$ARGUMENTS` 兼容、`` !`echo hi` `` 注入执行结果、危险注入命令（`rm -rf`）被拒并在结果中注明、旧格式命令（仅 description）仍可用
- [x] F5 /compact 手动压缩命令
  - id: task-compact
  - boundary: `src/nexuscli/context/manager.py`（公开 force-compress 入口，如缺失）、`src/nexuscli/entrypoints/repl.py`（`/compact [focus]` 命令与统计输出）、`tests/test_context.py`（扩充）
  - verify: `uv run python -m pytest tests/test_context.py -q` 通过，覆盖：手动压缩返回前后 token 统计、focus 主题进入摘要指令、历史条数下降、空历史安全处理
- [x] F6 REPL plan 模式三态循环
  - id: task-plan-mode
  - depends-on: task-permissions
  - boundary: `src/nexuscli/tools/executor.py` 或 `tools/base.py`（plan 态非只读工具拒绝）、`src/nexuscli/entrypoints/repl.py`（Shift+Tab 三态循环与状态显示）、`tests/test_plan_mode.py`（新建）
  - verify: `uv run python -m pytest tests/test_plan_mode.py -q` 通过，覆盖：plan 态 `write_file`/`bash` 被拒且提示只读、`read_file`/`grep` 放行、三态循环顺序 default→auto→plan→default、auto 态行为与现状一致（回归）

## 阶段三：集成与验收
- [x] 全链路集成与 REPL smoke
  - depends-on: task-permissions, task-hooks, task-subagent, task-slash-commands, task-compact, task-plan-mode
  - boundary: 只修集成缝隙（六项功能互相作用处：权限规则×hooks×plan 态叠加、task 工具在 plan 态被拒、命令 mode=plan 的会话态切换），不顺手重构
  - verify: 全量 `uv run python -m pytest -q` 全绿 + `uv run nexuscli --help` 正常 + 手动 smoke：`/help` 列出 `/compact`，配置示例 hooks/permissions 后 `--plain -p` 冒烟不报错
- [x] 文档同步（README/TUTORIAL//help）
  - depends-on: task_0009
  - boundary: 只更新 `README.md`、`TUTORIAL.md`、repl `/help` 文本与受影响章节；不改代码
  - verify: README 含六项功能小节与 hooks/permissions 配置示例；`/help` 输出含 `/compact`；`uv run python -m ruff check .` 通过（文档内代码块无误）
- [x] 完成验收检查并补齐证据
  - depends-on: task_0010
  - boundary: 只更新任务包文档、检查项与验收证据
  - verify: `checklist.md` 全勾且含至少一条真实命令证据（pytest/ruff/build 输出）；`python scripts/check_spec_package.py`（spec skill 脚本）校验通过

---

**当前进度**：由脚本计算，无需手动维护
