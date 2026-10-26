# 功能测试矩阵：要测什么，谁来测，测过没有

> 这份文档回答三个问题：**产品有哪些面向用户的表面**、**每个表面一个真人会怎么用**、**这个表面到 2026-10-18 为止有没有被真实跑过**。
>
> 判定口径只有两种：
> - **机器验证**：`tests/` 里的自动化断言（可重复、进 CI）
> - **真人模拟**：用真实 pty / 真实网关 / 真实外部服务跑一遍用户会做的事（一次性、靠证据留痕）
>
> 只有机器验证不算"测过"——断言能证明代码路径通，不能证明用户拿到的观感、文案、错误提示是对的。

## 图例

| 标记 | 含义 |
|---|---|
| ✅ 已测 | 有机器验证，且至少一次真实运行留痕 |
| 🟡 仅机器 | 有自动化断言，没有真实运行记录 |
| 🟠 仅真人 | 真实跑过，没有自动化断言（回归会静默退化） |
| ⬜ 未测 | 两条都没有 |

> 2026-10-18 第二轮（`fix-audit-residual`）之后，表中带 【本轮】 的行是这一轮补的真实运行/机器验证；带 【已修】 的缺陷行是本轮修掉并复测过的。
>
> 2026-10-19 收口轮（`verify-zero-and-yellow`，两轮工作流 + 轮间修复）：带 【收口】 的行是这两轮补齐的 62 个表面（17 ⬜ + 45 🟡 对应行，含 /history 主线程补测）。证据明细在 `.spec/specs/2026-10-18_verify-zero-and-yellow/evidence/`；两轮共修复 13 个真实运行暴露的缺陷，全部带回归测试。

---

## 一、启动与入口

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 1.1 | `nexuscli` 交互 REPL 冷启动 | 打开终端直接敲命令，看首帧多久出来、有没有闪烁 | `test_repl.py` | pty 实测首字节 0.377s，banner 两行各绘制 1 次 | ✅ |
| 1.2 | `nexuscli -p "..."` 单次模式 | 脚本里跑一句话，要能拿到干净 stdout | `test_cli.py`、`test_cli_single_prompt.py` | 真实网关多轮实测 | ✅ |
| 1.3 | `--json` 机器可读输出 | CI 里 `jq` 取 usage/cost | `test_cli.py` | `json.loads` 全量校验通过 | ✅ |
| 1.4 | `--plain` 无 ANSI 输出 | 管道给文件或别的工具 | `test_render.py` | `od -c` 确认无转义序列 | ✅ |
| 1.5 | 非 TTY stdin | `echo hi \| nexuscli`，期望友好提示 + exit 1 | `test_repl_non_tty.py` | 实测 exit=1 且提示指向 `-p` | ✅ |
| 1.6 | `--resume <id>` / `-c` 跨进程恢复 | 昨天聊到一半，今天接着聊 | `test_session_store.py` | 真实网关：能答出上一会话的 codeword | ✅ |
| 1.7 | 【收口】`doctor` 环境自检 | 装完第一件事就是跑它看缺什么 | `test_doctor_surface.py` | 实测输出 Python/uv/Node/rg/key 五项;子进程断言五节结构 | ✅ |
| 1.8 | `sessions` 列表 | 找回上周那个会话 | `test_session_store.py` | 实测列出会话 | ✅ |
| 1.9 | 【收口】`--version` | 报 bug 时贴版本 | `test_plain_exits.py` | 实测输出;断言动态跟随 `__version__` | ✅ |
| 1.10 | 【本轮】`-p --yes` 非交互放行 | CI 里让它真的跑命令 | `test_cli_single_prompt.py` | 实测 `touch made.txt` 真的落盘；不带 `--yes` 仍被拒 | ✅ |
| 1.11 | 【本轮】`-p` 端口占用/启动错误 | 端口撞了要知道换哪个 | `test_cli.py` | 实测 `Port 63644 is already in use. Try --port <other>.`，无 traceback | ✅ |

## 二、Agent 运行模式

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 2.1 | ReAct 循环 + 工具调用 | "帮我看看这个仓库用了什么依赖" | `test_agent_turns.py` | 真实网关多轮、工具返回事实正确 | ✅ |
| 2.2 | thinking 流式展示 | 复杂任务时看模型在想什么 | `test_render.py` | 真实网关可见 `thinking…` 增量 | ✅ |
| 2.3 | Plan-and-Execute（`--mode plan` / `/plan`） | 让它先规划再动手 | `test_plan.py`、`test_plan_checkpoint.py` | 实测 DAG 跑通，checkpoint 落盘 | ✅ |
| 2.4 | plan 只读闸门（`Shift+Tab` → plan） | 只读调研，确认方案再执行 | `test_plan_mode.py` | pty 实测写工具被硬拒、不写审计 | ✅ |
| 2.5 | `--mode plan` 的只读语义 | 同上，但走单次模式 | `test_cli.py` | 本轮修复后实测写入被拒 | ✅ |
| 2.6 | Multi-Agent team 模式 | "并行审计这几个模块" | `test_multi_agent.py` | 真实网关跑通，Phase 1/2 结构正确 | ✅ |
| 2.7 | 【收口】team 的 worker-mode=plan | worker 内部再走 plan | `test_lane_c_modes.py` | pty 实测 `--mode plan` 写工具被硬拒;修复 plan worker 从不置位 `plan_mode` 的缺陷;用户 pin 优先于 planner 步级 mode(round2 复验) | ✅ |
| 2.8 | 【收口】工具循环检测 | 模型卡在同一个调用上要能止损 | `test_loop_detection.py` | 真实网关单回合 6 次同参 list_dir,nudge→abort 止损信息可读 | ✅ |
| 2.9 | 【收口】provider 超限自动压缩重试 | 长会话撞 context 上限 | `test_overflow_retry.py` | 生产 react 循环+真实压缩器;provider 错误为 client 接缝注入(网关实测 600KB/1.4MB 均接受,无法自然触发,如实标注) | ✅ |
| 2.10 | 【收口】中断后续跑（`/plan --resume`） | 计划跑到一半 Ctrl+C，第二天续 | `test_plan_checkpoint.py` | pty 实测 task_2 中断后续跑跳过 task_1;摘要「首批任务」指向未完成批次(round2 复验) | ✅ |

## 三、REPL 交互表面

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 3.1 | 底部状态栏（模型 · effort · 上下文条 · cwd · 权限） | 随时知道自己在什么状态 | `test_render.py` | pty 实测 120/60/48 列三档 | ✅ |
| 3.2 | `Shift+Tab` 三态循环 | 临时切 auto 放行、切 plan 只读 | `test_permission_mode.py` | pty 实测 Default→Auto→plan→Default | ✅ |
| 3.3 | 【已修】窄终端下的状态栏（≤60 列） | 分屏或小窗口里用 | `test_render.py` | 48 列 pty 实测模式名保留：`cbcn/glm-5.3-flash  Default  ⇧Tab` | ✅ |
| 3.4 | 回合中途 Ctrl+C | 发现方向错了立刻打断 | 无（上一轮补的 pty 验证） | pty 实测：修复前杀进程，修复后中止回合且 `/exit` 正常 | ✅ |
| 3.5 | 【收口】空 prompt 上 Ctrl+C | 想清屏/退出 | `test_plain_exits.py` | pty 实测 exit=0;自动化同断言(POSIX skipif win32) | ✅ |
| 3.6 | 【收口】Ctrl+D 退出 | 惯用退出键 | `test_plain_exits.py` | pty 实测 exit=0;自动化同断言 | ✅ |
| 3.7 | 【已修】斜杠命令 Tab 补全 | 打 `/sk` 按 Tab 补成 `/skill` | `test_slash_completion.py` | pty 实测 `/sk<Tab>` → `/skill` | ✅ |
| 3.8 | 【已修】placeholder 文案 | 照着提示敲图片路径 | `test_repl.py` | pty 实测 `Type your message or @image:path/to/file` | ✅ |
| 3.9 | 中文输入输出 | 全程中文对话 | `test_render.py` | pty 实测 60 列无超宽 | ✅ |
| 3.10 | 【收口】bracketed paste | 粘贴多行代码 | `test_plain_exits.py` | pty 实测换行保留;自动化同断言 | ✅ |
| 3.11 | 长回答（>200 行） | 让它输出一份清单 | `test_render.py` | 实测换行曾折叠成一段（上一轮修复） | ✅ |
| 3.12 | 【本轮】`/clear` 重绘 | 换话题，清上下文 | `test_repl_ux.py` | pty 实测重绘 banner（35 行输出） | ✅ |
| 3.13 | 【本轮】`/compact` 压缩预算 | 主动瘦身 | `test_context.py` | pty 实测空历史回 `(no conversation history to compact)`；有历史时压缩后必须变小，否则如实报 | ✅ |

## 四、斜杠命令（31 个）

| 命令 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|
| `/help` | 忘了命令名 | 1 | pty 实测列出全部命令 | ✅ |
| `/exit` | 收工 | 1 | pty 实测 exit=0 | ✅ |
| `/clear` | 换话题，清上下文 | 2 | 【本轮】pty 实测重绘 banner | ✅ |
| `/resume` | 切回某个历史会话 | 3 | pty 实测列表可读、`/resume 1` 恢复行带 id/条数/标题,跨进程答出上一会话暗号 | ✅ |
| `/history` | 找上周问过的问题 | 1 | pty 实测 `/history 紫罗兰 --all` 4 列表格命中刚问的问题(evidence/main-history.md) | ✅ |
| `/context` | 看当前上下文占了多少 | `test_slash_surfaces_docs.py` | pty 实测表格正确;直驱断言可读 | ✅ |
| `/compact` | 手动压一次上下文 | 1 | 【本轮】pty 实测空历史如实回报 | ✅ |
| `/memory` | 看记住了什么 | `test_lane_b_memory_surface.py` | pty 实测列表/搜索/统计/删除各态可读 | ✅ |
| `/save` | 存一条长期记忆(`<fact>` 进 MemoryManager;会话本身由回合自动持久化) | `test_lane_a_session_surface.py` | pty 实测 `Saved memory #1` 可读,sessions 列表由自动持久化覆盖(行描述按实现语义更正) | ✅ |
| `/config` | 核对生效配置 | 0 | 【本轮】pty 实测摘要 43 行、无 JSON dump | ✅ |
| `/tools` | 看有哪些工具 | `test_slash_surfaces_docs.py` | pty 实测列出 27 个;直驱断言 | ✅ |
| `/hitl` | 切审批档位 | `test_lane_e_tool_surfaces.py` | pty 实测切档、状态栏、错误提示联动;`/hitl never` 下带 `2>/dev/null` 纯读命令零审批直通(round2 复验) | ✅ |
| `/policy` | 核对安全策略 | `test_slash_surfaces_docs.py` | pty 实测摘要;直驱断言 | ✅ |
| `/audit` | 查刚才那条为什么被拒 | 0 | 【本轮】pty 实测输出 4 列摘要表 | ✅ |
| `/index` | 建代码索引 | `test_lane_b_index_search.py` | pty 实测计数与源文件非空行一致 | ✅ |
| `/search` | 语义搜代码 | `test_lane_b_index_search.py` | pty 实测命中 path:line 与源码一致 | ✅ |
| `/init` | 生成 AGENTS.md | 1 | pty 实测生成内容与仓库实际相符(入口由模型实跑验证) | ✅ |
| `/plan` | 进入 Plan-and-Execute | 1 | 真实网关跑通 | ✅ |
| `/team` | 进入多代理 | 0 | pty 实测 REPL 内最小规模真实网关跑通,Phase 1/2 可读;`--mode plan` 用户 pin 生效(round2) | ✅ |
| `/model` | 切模型 | `test_model_selector.py`、`test_lane_e_tool_surfaces.py` | pty 实测上下选/回车切换/Esc 取消、无密钥报对变量名;拉取列表含 视觉✓ 徽标(round2 补) | ✅ |
| `/usage` | 看这轮烧了多少 token | 2、`test_slash_surfaces_docs.py` | pty 实测四行状态表;直驱断言网格行内容 | ✅ |
| `/skill` | 加载技能 | 2 | 【已修】pty 实测 `Unknown skill: "nosuchskill". Available: web-access` | ✅ |
| `/mcp` | 看接了哪些 MCP | `test_repl_mcp_status.py` | pty 实测列出 server 名/类型/命令/工具数、disabled、error 行(F3 修复后 round2 复验) | ✅ |
| `/task` | 派子代理(持久任务队列) | `test_lane_c_team_task.py` | pty 实测入队→真实 worker 消费→`/task log` 取回结果 | ✅ |
| `/workflow` | 跑 JSON 步骤图 | 1 | pty 实测 3 步 DAG 并行分批跑通,三份报告可读 | ✅ |
| `/expert` | 内置三步专家流程 | 2 | pty 实测 research→execute→report 真实网关走通 | ✅ |
| `/fork` | 分叉会话试另一个方向 | `test_lane_a_session_surface.py` | pty 实测分叉后主线逐字节未变、forked_from/usage 清零正确 | ✅ |
| `/goal` | 设跨回合长程目标 | 1 | pty 实测下回合回复带【GOAL-ON-DUTY】,目标落盘持久 | ✅ |
| `/effort` | 调推理力度 | 1 | pty 实测八次循环顺序正确；【本轮】`off` 在状态栏可见 | ✅ |
| `/snapshot` | 手动存快照(`save [label]`/列表/clean) | `test_lane_a_snapshot_restore.py` | pty 实测 save→列表→restore→clean 全闭环文案可读(round2 复验);修复了默认关自动快照时无手动建快照入口的缺陷 | ✅ |
| `/restore` | 回滚到某个快照 | `test_lane_a_snapshot_restore.py` | pty 实测文件内容还原、pre-restore 安全快照新增、错误路径可读 | ✅ |

## 五、内置工具（25 个 + 2 个 vfs 视图）

| 工具 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|
| `read_file` | "读一下这个文件" | 18 | 真实网关跑过；越界被拒 | ✅ |
| `write_file` | "把这个写进去" | 9 | 真实网关跑过（补了审批） | ✅ |
| `edit_file` | "把这行改掉" | 4 | 真实网关实测两行编辑,第二行未动,落盘核对 | ✅ |
| `list_dir` | "这个目录有啥" | 4 | 真实网关跑过 | ✅ |
| `glob` / `glob_files` | "找出所有测试文件" | 3 | 真实网关跑过 | ✅ |
| `grep` / `grep_code` | "哪里用了这个函数" | 6 | 真实网关跑过 | ✅ |
| `directory_tree` | "给我看目录树" | `test_lane_e_tool_surfaces.py` | 真实网关实测树正确、`.git` 排除、连接符可读 | ✅ |
| `get_file_info` | "这个文件多大" | `test_lane_e_tool_surfaces.py` | 真实网关实测与 stat 一致;修复 Modified 由裸 epoch 改为本地时间 | ✅ |
| `bash` / `execute_command` | "跑一下测试" | 17 / 6 | 真实网关跑过（含拒绝路径） | ✅ |
| `web_search` | "搜一下这个报错" | 1 | 【已修】真实检索返回 5 条；失败时报 `DuckDuckGo request failed (ConnectError) for query 'anything': All connection attempts failed` | ✅ |
| `web_fetch` | "看看这个链接" | 2 | 真实跑过（修了 fake-IP 误杀） | ✅ |
| `save_memory` / `search_memory` | "记住这个偏好" | 2 / 1 | 真实网关:save 落盘(source=agent)、新进程 search 原样召回 | ✅ |
| `load_skill` / `save_skill` | 加载/沉淀技能 | 1 / 3 | 跨进程双会话:save 落盘→新进程 load 注入→按技能指示输出 | ✅ |
| `search_code` | 语义搜代码 | `test_lane_e_tool_surfaces.py` | 先建索引后真实检索命中+统计行;空索引提示先 `/index` | ✅ |
| `task_output` / `task_stop` | 看/停后台任务 | 2 / 2 | 【已修】实测 `sh -c 'sleep 30 & sleep 30'` 停止后进程组为空 | ✅ |
| `revert_turn` | 撤销上一轮改动 | 2 | 真实网关实测文件精确回到改前 | ✅ |
| `todo_write` / `todo_read` | 任务清单 | `test_lane_e_tool_surfaces.py` | 写入读回一致、todo.json 落盘匹配、REPL 展示可读 | ✅ |
| `task`（子代理） | "派个子代理去查" | 3 | 真实 `-p`:主代理派子代理查测试函数,返回事实与 ground truth 一致 | ✅ |
| `vfs_ls` / `vfs_read` | 只读看历史快照 | 13 | 真实网关回合中模型调用,返回改前内容、未误读工作区现值 | ✅ |

## 六、权限与安全

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 6.1 | 审批四档 y/r/a/s/n | 逐条决定放不放 | `test_permissions.py` | pty 实测 r 档 run 级授权生效 | ✅ |
| 6.2 | 审批提示可退出（Esc/Ctrl+C） | 反悔，不想批了 | `test_approval_keys.py` | pty 实测修复前卡死、修复后 deny | ✅ |
| 6.3 | `permissions.allow/deny/ask` | 把 `npm test` 设成免问 | `test_permissions.py` | 真实 `-p` 实测 allow 放行 | ✅ |
| 6.4 | 只读 bash 直通 | `git status` 不该弹窗 | `test_bash_readonly.py` | 实测工作区内 `cat` 直通、凭证文件被拦 | ✅ |
| 6.5 | 路径守卫 | 别让它写到项目外 | `test_fs_scope.py` | 实测 `..` 与 symlink 均被拒 | ✅ |
| 6.6 | 环境变量清洗 | 别把 API key 漏给子进程 | `test_sandbox_guard.py` | 实测子进程看不到 KEY/TOKEN | ✅ |
| 6.7 | 【已修】审计日志 | 事后查谁批了什么 | `test_audit_redaction.py` | 实测文件 `0o600`、正文截断 500 字符、`sk-` 形态掩码 | ✅ |
| 6.8 | 【收口】Hooks 七事件 | 在工具调用前跑自己的检查 | `test_hooks.py` | 真实 hook 脚本七事件全触发:SessionStart1/UserPromptSubmit2/PreToolUse2/PostToolUse1/PostToolUseFailure1/PermissionRequest2/Stop2 | ✅ |
| 6.9 | 【收口】工作区 hooks 信任门禁 | clone 别人的仓库先审 hooks | `test_hooks_trust.py` | n 拒→hooks 不执行、y 信→执行,信任记录含 sha256 持久化 | ✅ |
| 6.10 | plan 态只读 | 见 2.4 | `test_plan_mode.py` | pty 实测 | ✅ |
| 6.11 | `-p` 审批默认拒绝 + `--yes` 显式放行 | 脚本里跑要写文件 | `test_cli_single_prompt.py` | 实测默认被拒、`--yes` 放行 | ✅ |
| 6.12 | 【已修】Runtime API 审批通道 | 机器人接进来跑任务 | `test_runtime.py` | 实测 `write_file` 被拒且文件未落盘 | ✅ |
| 6.13 | 【已修】MCP server 浏览器安全 | 本机跑着别被网页打 | `test_mcp_http_security.py` | 实测 GET 405 / 错路径 404 / text/plain 415 / 跨源 403 / 同源 200 / 缺错 key 401 | ✅ |
| 6.14 | 【已修】MCP HTTP 凭证门 | 无 key 的本机进程想白嫖工具 | `test_mcp_http_security.py` | 实测缺/错 key 401、未配置 `NEXUSCLI_MCP_API_KEY` 拒绝启动、带 key 调用落审计 | ✅ |
| 6.15 | 【已修】写域守卫尊重 full-access | REPL Auto 模式下域外写被自相矛盾地拦 | `test_fs_scope.py` | 实测 Auto 组合不追问、headless 无回调仍 fail-closed | ✅ |
| 6.16 | 【已修】hooks 三路径下沉 | /plan /team 绕过 UserPromptSubmit 门 | `test_hooks_lane_coverage.py` | 实测 plan/team 入口 exit 2 即拒且不调 LLM | ✅ |

## 七、记忆与上下文

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 7.1 | 【收口】静态记忆（AGENTS.md/NEXUS.md） | 项目约定写进文件 | `test_prompt.py` | 真实 `-p` 问杜撰代号 BLUE-OTTER-77,隔离 HOME 下唯一来源即 `<project-instructions>` | ✅ |
| 7.2 | 【收口】动态长期记忆（SQLite） | "记住我用 uv 不用 pip" | `test_memory.py` | 会话1 save_memory 落盘(sqlite 核对),会话2 全新进程逐字答出 bun 偏好;`<project-constraints>` 受信块措辞加强后 2/2 带出(round2) | ✅ |
| 7.3 | 【收口】相关性召回 Top-K | 不问也能想起相关约束 | `test_memory.py` | 播种 [OTTER-42] 前缀约定,不点名提问被主动带出并处理与 AGENTS.md 的冲突 | ✅ |
| 7.4 | 【收口】自动压缩（80% 触发） | 长会话不炸 | `test_context.py` | 真实触发压缩 52469→12991 token;修复摘要 assistant 角色被严格交替网关 400 的缺陷,修复后压缩+续聊可用 | ✅ |
| 7.5 | 【已修】`/compact` 手动压缩 | 主动瘦身 | `test_context.py` | pty 实测空历史如实回报；压缩必须变小 | ✅ |
| 7.6 | 【收口】代码索引与检索 | "搜一下这个函数在哪" | `test_search_rank.py` | pty /index 落盘后真实 `-p` 让模型 search_code,答出 app.py 第 8 行,与 /search 互相印证 | ✅ |

## 八、会话与持久化

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 8.1 | 会话转录 JSONL | 关掉再开还在 | `test_session_store.py` | 实测落盘 | ✅ |
| 8.2 | usage 随转录持久化 | 恢复后 token 接着算 | `test_session_usage.py` | 实测双口径正确 | ✅ |
| 8.3 | 【收口】`/fork` 分叉 | 试另一个方向不污染主线 | `test_lane_a_session_surface.py` | pty 实测主线逐字节未变、分叉回合只进分叉 transcript | ✅ |
| 8.4 | 【收口】快照 / `/snapshot` / `/restore` | 改坏了回滚 | `test_snapshot.py`、`test_lane_a_snapshot_restore.py` | create→index→跨进程 resolve→restore(含 pre-restore 安全快照)全链路真实走通 | ✅ |
| 8.5 | 【收口】vfs 只读视图 | 对比改前改后 | `test_vfs.py` | 真实网关回合中 vfs_ls/vfs_read 返回改前内容 | ✅ |
| 8.6 | 【已修】自动快照（pre/post turn） | 想要每回合回滚点 | `test_config.py` | 实测默认关；README 已写明开关与默认值 | ✅ |

## 九、联网

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 9.1 | `web_fetch` 抓正文 | "看看这个链接讲了啥" | `test_web_fetch_policy.py` | 实测 GitHub/python.org 取回正文 | ✅ |
| 9.2 | SSRF 守卫 | 别让它抓内网 | `test_web_fetch_policy.py` | 实测 loopback/RFC1918/metadata 被拒 | ✅ |
| 9.3 | 【已修】`web_search` | 搜最新报错 | `test_web_fetch_policy.py` | 真实检索 5 条；失败时给出类型与原因 | ✅ |
| 9.4 | 系统代理环境 | 公司网/梯子下能用 | `test_proxy_bypass.py` | 实测回环旁路正确 | ✅ |

## 十、MCP

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 10.1 | MCP client（stdio / HTTP） | 接 filesystem server | `test_mcp.py` | 实测 npx server 可接入 | ✅ |
| 10.2 | 【收口】工具级过滤 allowed/blocked | 只放几个工具进来 | `test_mcp_filter.py` | 真实 stdio server 实测只放行的工具可见 | ✅ |
| 10.3 | 【收口】OAuth 令牌落盘 0600 | 接需要授权的 server | `test_mcp_oauth.py` | 本地假 OAuth server 走完整流程,令牌文件 0600 | ✅ |
| 10.4 | 自身作为 MCP server | 让别的客户端用 NexusCLI | `test_mcp_server_protocol.py` | 官方 SDK 握手通过 | ✅ |
| 10.5 | 【已修】MCP server 浏览器安全 | 本机跑着别被网页打 | `test_mcp_http_security.py` | 实测 405/404/415/403 全部生效 | ✅ |
| 10.6 | 【已修】MCP client 连接复用 | 有状态 server 要保活 | `test_mcp.py` | 实测三次调用同一 pid，count 1→2→3 | ✅ |
| 10.7 | `mcp list` / `init-chrome` | 看配了啥 | `test_mcp_filter.py` | 【已修】实测展示 disabled；配置损坏报 `Invalid MCP config` + exit 1 | ✅ |
| 10.8 | 【已修】MCP 工具入参本地校验 | 缺必填参数别发出去 | `test_mcp_filter.py` | 实测 `missing required input: name. Not sent.` | ✅ |

## 十一、Runtime API / cron / eval / SDK

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 11.1 | `serve --http` + API key | 机器人接进来 | `test_runtime.py` | 实测 200/401 正确 | ✅ |
| 11.2 | `worker` 消费任务 | 后台跑长任务 | `test_runtime.py` | 实测同项目完成 | ✅ |
| 11.3 | 任务租约与取消 | 崩溃后自动重排 | `test_runtime.py` | 实测过期租约重排成功 | ✅ |
| 11.4 | 【已修】Runtime 并发 turn | 同时发两个请求 | `test_runtime.py` | 线程锁串行化 | ✅ |
| 11.5 | 【已修】不存在的 thread | 请求打错 id | `test_runtime.py` | 实测 404 `thread not found` | ✅ |
| 11.6 | 【已修】`cron list/next/run` | 定时跑日报 | `test_cron.py` | 实测 `[disabled] dead`；`cron run` 单行可直接 `$(...)` | ✅ |
| 11.7 | `eval list/run` | 改坏了看回归 | `test_eval.py` | 实测 3/3 通过 | ✅ |
| 11.8 | 【已修】eval 在 wheel 形态 | `pip install` 后跑 eval | `test_eval.py` | 内置场景随包分发 | ✅ |
| 11.9 | 【收口】SDK `create_default_engine` | 在 Python 里调 | `test_lane_e_mcp_sdk.py` | 真实创建引擎并跑通一个 turn | ✅ |

## 十二、扩展机制

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 12.1 | 【收口】Skill 系统（builtin/user/project） | 装个团队规范 | `test_skill.py` | 三层真实加载且覆盖链正确:user 覆盖 builtin、project 覆盖 user | ✅ |
| 12.2 | 【收口】Agent Skills 规范兼容 | 直接用社区 skill | `test_skill_spec_compat.py` | 社区格式(frontmatter allowed-tools 等)直接可用 | ✅ |
| 12.3 | 【收口】自定义斜杠命令 | 把常用提示词存起来 | `test_lane_d_extensions.py` | pty 实测 `/release-note v9.9.9` 提示词经网关生效,mode/allowed-tools 运行期生效且还原 | ✅ |
| 12.4 | 【收口】插件（`.nexuscli/plugins/`） | 打包一组 agents+skills+commands | `test_plugins.py` | 最小插件三类组件真实进程全部生效 | ✅ |
| 12.5 | 【收口】自定义子代理（`.nexuscli/agents/*.md`） | 定义专属 reviewer | `test_lane_d_extensions.py` | pty 实测 agent_type=reviewer 报告遵循 md 格式、system prompt 来自 md | ✅ |
| 12.6 | 【收口】动态工作流（JSON DAG） | 把固定流程存成文件 | `test_workflow.py` | 3 步图并行分批真实跑通,Workflow complete 可读 | ✅ |
| 12.7 | 【收口】子代理文件邮箱 | 两个子代理协作 | `test_subagent_mailbox.py` | 双子代理经 mailbox 交换数字,双方独立算出 42,inbox drain 清空 | ✅ |
| 12.8 | 【收口】后台子代理 + 完成播报 | 派出去就不管了 | `test_bg_subagent.py` | run_in_background 立即返回 id,回合末播报完成,报告落盘可查 | ✅ |

## 十三、可观测性

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 13.1 | 【收口】`trace_id` 关联 | 把一次请求的所有日志串起来 | `test_trace_id.py` | pty 真实会话 audit trace_id == 网关 x-request-id | ✅ |
| 13.2 | 【收口】OTel span | 接自己的 collector | `test_telemetry.py` | 真实 SDK provider+exporter 导出成功;无 collector 时 NonRecording 边界如实 | ✅ |
| 13.3 | 【收口】`llm.debug_dump` | 排查请求长什么样 | `test_llm_io_dump.py` | 真实 dump 落盘、敏感头脱敏、请求结构可读 | ✅ |
| 13.4 | 【收口】`/usage stats` 落库 | 看这周花了多少 | `test_usage_coverage.py` | 双进程数字一致,重启持久 | ✅ |

## 十四、图片与 PDF

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 14.1 | 【收口】`@image:` 本地图片 | 贴截图让它看 | `test_vision_capability.py`、`test_lane_f_media.py` | round2 pty 真实网关端到端:答「蓝色背景上有一个黄色的圆形」,降级行不再出现(supports_images 改能力驱动后解锁) | ✅ |
| 14.2 | 【收口】远程图片 | 贴 URL | `test_lane_f_media.py` | CLI 直传正确(wire dump 证实);上游网关当前拒收一切远程 URL(11135,含 round1 成功过的图),待上游恢复复跑端到端 | 🟡 |
| 14.3 | 【收口】非多模态模型降级 | 用 deepseek 时别报错 | `test_lane_f_media.py` | 显式 `llm.vision=false` 端到端:不崩、提示可读、payload 退化为纯文本(round2 复验) | ✅ |
| 14.4 | 【收口】PDF 读取 | 让它读需求文档 | `test_file_read_pdf.py` | 造双页 PDF,票号/活动名答对 | ✅ |
| 14.5 | 【收口】Python 语法诊断 | 写完自动查错 | `test_lsp.py` | 真实 agent 会话原样展示行号+原因 | ✅ |

---

## 十五、汇总

| 状态 | 数量 | 含义 |
|---|---|---|
| ✅ 已测 | 146 | 机器验证 + 真实运行都有 |
| 🟠 仅真人 | 0 | —（2026-10-19 发布收口轮补齐九项断言） |
| 🟡 仅机器 | 1 | 有断言，真实端到端被上游环境卡住（14.2 远程图片，CLI 契约 wire 级已证） |
| ⬜ 未测 | 0 | — |
| **合计** | **147** | |

### 收口轮（`verify-zero-and-yellow`，两轮工作流）补上的验证

- **17 个 ⬜ 全部双补**：/model 选择器（TUI 交互 + 视觉徽标）、/task、/snapshot save+/restore（补了手动建快照入口）、/fork、/mcp（补了 server 列表渲染）、/memory、/index+/search、/hitl、/save（行描述按实现语义更正为长期记忆）、directory_tree、get_file_info、search_code、todo_write/read、team worker-mode=plan、/fork(8.3)、SDK create_default_engine。
- **45 个 🟡 中 44 个补真实运行**：/team（REPL 内）、/workflow、/expert、/goal、/history、插件、自定义命令、hooks 七事件、@image: 端到端、PDF、LSP、全部扩展机制与可观测性面。
- **真实运行暴露并修复的缺陷（13 个，全部带回归测试）**：plan worker 可写（2.7）、压缩摘要 assistant 角色被网关拒（7.4）、/snapshot 无手动入口、rich markup 吞 `[label]`、get_file_info 裸 epoch、写域守卫无视 allow 且误报 /dev/null、/mcp 不列 server、supports_images 名称门槛挡住真实 vision 模型、planner 静默覆盖用户 --worker-mode、plan resume 摘要误导、/model 拉取列表缺视觉徽标、审批提示不渲染原因、<recalled-memory> 泛化措辞约束力弱（改受信 `<project-constraints>` 块）。

### 仍然没测的

1. **14.2 远程图片端到端**：上游网关当前拒收一切远程图片 URL（CLI 直传契约已由 wire dump 证实正确），待上游恢复复跑即可置绿。
2. **低危接受项**：loop 止损文案单复数（"1 turns"）、/expert 中游子代理空产出仅在最终报告可见、弱模型对召回约束的遵守是概率性的（对抗问法下不保证）、站点 .brand-logo 渐变白字（WCAG logo 豁免）。

---

## 附：怎么复现这张表

```bash
# 机器验证：全量
uv run python -m pytest

# 机器验证：单个表面
uv run python -m pytest tests/test_repl.py tests/test_render.py

# 真实运行：单次模式（需要已配置 API key）
uv run nexuscli -p "列出 tests 目录下两个测试文件名" --json

# 真实运行：交互模式（需要真终端，不能用管道）
uv run nexuscli
```

真实运行没有脚本化入口是刻意的：pty 驱动只能验证渲染与按键，验证不了"这个错误提示读起来是否讲得通"。那一层只能人看。
