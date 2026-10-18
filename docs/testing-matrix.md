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

---

## 一、启动与入口

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 1.1 | `nexuscli` 交互 REPL 冷启动 | 打开终端直接敲命令，看首帧多久出来、有没有闪烁 | `test_repl.py` | pty 实测首字节 0.377s，banner 两行各绘制 1 次 | ✅ |
| 1.2 | `nexuscli -p "..."` 单次模式 | 脚本里跑一句话，要能拿到干净 stdout | `test_cli.py`、`test_cli_single_prompt.py` | 真实网关 3 轮实测 | ✅ |
| 1.3 | `--json` 机器可读输出 | CI 里 `jq` 取 usage/cost | `test_cli.py` | `json.loads` 全量校验通过 | ✅ |
| 1.4 | `--plain` 无 ANSI 输出 | 管道给文件或别的工具 | `test_render.py` | `od -c` 确认无转义序列 | ✅ |
| 1.5 | 非 TTY stdin | `echo hi \| nexuscli`，期望友好提示 + exit 1 | `test_repl_non_tty.py` | 实测 exit=1 且提示指向 `-p` | ✅ |
| 1.6 | `--resume <id>` / `-c` 跨进程恢复 | 昨天聊到一半，今天接着聊 | `test_session_store.py` | 真实网关：能答出上一会话的 codeword | ✅ |
| 1.7 | `doctor` 环境自检 | 装完第一件事就是跑它看缺什么 | 无 | 实测输出 Python/uv/Node/rg/key 五项 | 🟠 |
| 1.8 | `sessions` 列表 | 找回上周那个会话 | `test_session_store.py` | 实测列出 2 条 | ✅ |
| 1.9 | `--version` | 报 bug 时贴版本 | 无 | 实测 `nexuscli 0.1.0` | 🟠 |

## 二、Agent 运行模式

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 2.1 | ReAct 循环 + 工具调用 | "帮我看看这个仓库用了什么依赖" | `test_agent_turns.py` | 真实网关 3 轮、工具返回事实正确 | ✅ |
| 2.2 | thinking 流式展示 | 复杂任务时看模型在想什么 | `test_render.py` | 真实网关可见 `thinking…` 增量 | ✅ |
| 2.3 | Plan-and-Execute（`--mode plan` / `/plan`） | 让它先规划再动手 | `test_plan.py`、`test_plan_checkpoint.py` | 实测 1 任务 DAG 跑通，checkpoint 落盘 | ✅ |
| 2.4 | plan 只读闸门（`Shift+Tab` → plan） | 只读调研，确认方案再执行 | `test_plan_mode.py` | pty 实测写工具被硬拒、不写审计 | ✅ |
| 2.5 | `--mode plan` 的只读语义 | 同上，但走单次模式 | `test_cli.py` | 本轮修复后实测写入被拒 | ✅ |
| 2.6 | Multi-Agent team 模式 | "并行审计这几个模块" | `test_multi_agent.py` | 真实网关 2m54s 跑通，Phase 1/2 结构正确 | ✅ |
| 2.7 | team 的 worker-mode=plan | worker 内部再走 plan | 无 | 未跑 | ⬜ |
| 2.8 | 工具循环检测 | 模型卡在同一个调用上要能止损 | `test_loop_detection.py` | 未跑（需要构造真实重复调用） | 🟡 |
| 2.9 | provider 超限自动压缩重试 | 长会话撞 context 上限 | `test_overflow_retry.py` | 未跑（需要构造超限） | 🟡 |
| 2.10 | 中断后续跑（`/plan --resume`） | 计划跑到一半 Ctrl+C，第二天续 | `test_plan_checkpoint.py` | 未跑 | 🟡 |

## 三、REPL 交互表面

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 3.1 | 底部状态栏（模型 · effort · 上下文条 · cwd · 权限） | 随时知道自己在什么状态 | `test_render.py` | pty 实测 120/60/40 列三档 | ✅ |
| 3.2 | `Shift+Tab` 三态循环 | 临时切 auto 放行、切 plan 只读 | `test_permission_mode.py` | pty 实测 Default→Auto→plan→Default | ✅ |
| 3.3 | 窄终端下的状态栏（≤60 列） | 分屏或小窗口里用 | 无 | pty 实测：模式名被裁掉，按 Shift+Tab 屏幕 0 变化 | ⬜ **缺陷** |
| 3.4 | 回合中途 Ctrl+C | 发现方向错了立刻打断 | 无（本轮补） | pty 实测：修复前杀进程，修复后中止回合 | ✅ |
| 3.5 | 空 prompt 上 Ctrl+C | 想清屏/退出 | 无 | pty 实测 exit=0 | 🟠 |
| 3.6 | Ctrl+D 退出 | 惯用退出键 | 无 | pty 实测 exit=0 | 🟠 |
| 3.7 | 斜杠命令 Tab 补全 | 打 `/sk` 按 Tab 补成 `/skill` | 无 | pty 实测：完全不补全 | ⬜ **缺陷** |
| 3.8 | placeholder 文案 `@path/to/file` | 照着提示敲 `@README.md` | 无 | 实测：真实语法是 `@image:`，文案骗人 | ⬜ **缺陷** |
| 3.9 | 中文输入输出 | 全程中文对话 | `test_render.py` | pty 实测 60 列无超宽 | ✅ |
| 3.10 | bracketed paste | 粘贴多行代码 | 无 | pty 实测换行保留 | 🟠 |
| 3.11 | 长回答（>200 行） | 让它输出一份清单 | `test_render.py` | 实测：换行被折叠成一段（本轮修复） | ✅ |

## 四、斜杠命令（31 个）

真实使用里只有少数几个每天用。下表按"真会用到"排序，`机器验证` 列是 `tests/` 里对该命令字符串的引用文件数。

| 命令 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|
| `/help` | 忘了命令名 | 1 | pty 实测列出 31 条 | ✅ |
| `/exit` | 收工 | 1 | pty 实测 exit=0 | ✅ |
| `/clear` | 换话题，清上下文 | 2 | pty 实测：banner 不重绘、上下文条不清零 | 🟠 **缺陷** |
| `/resume` | 切回某个历史会话 | 3 | 未在 REPL 内跑（`-c` 跑过） | 🟡 |
| `/history` | 找上周问过的问题 | 1 | 未跑 | 🟡 |
| `/context` | 看当前上下文占了多少 | 0 | pty 实测：6 行表格正确 | 🟠 |
| `/compact` | 手动压一次上下文 | 1 | pty 实测：越压越大（24→101→180→258） | ⬜ **缺陷** |
| `/memory` | 看记住了什么 | 0 | 未跑 | ⬜ |
| `/save` | 存当前会话 | 0 | 未跑 | ⬜ |
| `/config` | 核对生效配置 | 0 | pty 实测：整屏 JSON 刷屏 | 🟠 **缺陷** |
| `/tools` | 看有哪些工具 | 0 | pty 实测列出 27 个 | 🟠 |
| `/hitl` | 切审批档位 | 0 | 未跑 | ⬜ |
| `/policy` | 核对安全策略 | 0 | pty 实测：整屏 JSON 刷屏 | 🟠 **缺陷** |
| `/audit` | 查刚才那条为什么被拒 | 0 | pty 实测：输出真实审计记录 | 🟠 |
| `/index` | 建代码索引 | 0 | 未跑 | ⬜ |
| `/search` | 语义搜代码 | 0 | 未跑 | ⬜ |
| `/init` | 生成 AGENTS.md | 1 | 未跑 | 🟡 |
| `/plan` | 进入 Plan-and-Execute | 1 | 真实网关跑通 | ✅ |
| `/team` | 进入多代理 | 0 | 未跑（`--mode team` 跑过） | 🟡 |
| `/model` | 切模型 | 0 | 未跑（TUI 选择器） | ⬜ |
| `/usage` | 看这轮烧了多少 token | 2 | pty 实测四行状态表 | 🟠 |
| `/skill` | 加载技能 | 2 | 未跑（`/skill 未知` 静默列全表） | 🟡 **缺陷** |
| `/mcp` | 看接了哪些 MCP | 0 | 未跑 | ⬜ |
| `/task` | 派子代理 | 0 | 未跑 | ⬜ |
| `/workflow` | 跑 JSON 步骤图 | 1 | 未跑 | 🟡 |
| `/expert` | 内置三步专家流程 | 2 | 未跑 | 🟡 |
| `/fork` | 分叉会话试另一个方向 | 2 | 未跑 | 🟡 |
| `/goal` | 设跨回合长程目标 | 1 | 未跑 | 🟡 |
| `/effort` | 调推理力度 | 1 | pty 实测八次循环顺序正确 | ✅ |
| `/snapshot` | 手动存快照 | 0 | 未跑 | ⬜ |
| `/restore` | 回滚到某个快照 | 0 | 未跑 | ⬜ |

**结论**：31 个命令里，真正被真人跑过的 12 个，只有机器断言的 9 个，两条都没有的 10 个。

## 五、内置工具（25 个 + 2 个 vfs 视图）

| 工具 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|
| `read_file` | "读一下这个文件" | 18 | 真实网关跑过；越界被拒 | ✅ |
| `write_file` | "把这个写进去" | 9 | 真实网关跑过（本轮补审批） | ✅ |
| `edit_file` | "把这行改掉" | 4 | 未跑 | 🟡 |
| `list_dir` | "这个目录有啥" | 4 | 真实网关跑过 | ✅ |
| `glob` / `glob_files` | "找出所有测试文件" | 3 | 真实网关跑过 | ✅ |
| `grep` / `grep_code` | "哪里用了这个函数" | 6 | 真实网关跑过 | ✅ |
| `directory_tree` | "给我看目录树" | 0 | 未跑 | ⬜ |
| `get_file_info` | "这个文件多大" | 0 | 未跑 | ⬜ |
| `bash` / `execute_command` | "跑一下测试" | 17 / 6 | 真实网关跑过（含拒绝路径） | ✅ |
| `web_search` | "搜一下这个报错" | 1 | 未跑（失败时只回空 `Search error:`） | 🟡 **缺陷** |
| `web_fetch` | "看看这个链接" | 2 | 真实跑过（本轮修复 fake-IP 误杀） | ✅ |
| `save_memory` / `search_memory` | "记住这个偏好" | 2 / 1 | 未跑 | 🟡 |
| `load_skill` / `save_skill` | 加载/沉淀技能 | 1 / 3 | 未跑 | 🟡 |
| `search_code` | 语义搜代码 | 0 | 未跑 | ⬜ |
| `task_output` / `task_stop` | 看/停后台任务 | 2 / 2 | 未跑（`task_stop` 只杀直接子进程） | 🟡 **缺陷** |
| `revert_turn` | 撤销上一轮改动 | 2 | 未跑 | 🟡 |
| `todo_write` / `todo_read` | 任务清单 | 0 / 0 | 未跑 | ⬜ |
| `task`（子代理） | "派个子代理去查" | 3 | 未跑 | 🟡 |
| `vfs_ls` / `vfs_read` | 只读看历史快照 | 13 | 未跑 | 🟡 |

## 六、权限与安全

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 6.1 | 审批四档 y/r/a/s/n | 逐条决定放不放 | `test_permissions.py` | pty 实测 r 档 run 级授权生效 | ✅ |
| 6.2 | 审批提示可退出（Esc/Ctrl+C） | 反悔，不想批了 | 无（本轮补） | pty 实测修复前卡死、修复后 deny | ✅ |
| 6.3 | `permissions.allow/deny/ask` | 把 `npm test` 设成免问 | `test_permissions.py` | 真实 `-p` 实测 allow 放行 | ✅ |
| 6.4 | 只读 bash 直通 | `git status` 不该弹窗 | `test_bash_readonly.py`（99 项） | 实测工作区内 `cat` 直通、凭证文件被拦 | ✅ |
| 6.5 | 路径守卫 | 别让它写到项目外 | `test_fs_scope.py`（61 项） | 实测 `..` 与 symlink 均被拒 | ✅ |
| 6.6 | 环境变量清洗 | 别把 API key 漏给子进程 | `test_sandbox_guard.py` | 实测子进程看不到 KEY/TOKEN | ✅ |
| 6.7 | 审计日志 | 事后查谁批了什么 | 无 | 实测记录真实；但原文落盘 + 0644 | 🟠 **缺陷** |
| 6.8 | Hooks 七事件 | 在工具调用前跑自己的检查 | `test_hooks.py`（34 项） | 未跑真实 hook | 🟡 |
| 6.9 | 工作区 hooks 信任门禁 | clone 别人的仓库先审 hooks | `test_hooks_trust.py` | 未跑 | 🟡 |
| 6.10 | plan 态只读 | 见 2.4 | `test_plan_mode.py` | pty 实测 | ✅ |
| 6.11 | `-p` 无审批回调 | 脚本里跑要写文件 | 无 | 实测被拒；本轮补提示与文档 | 🟠 **设计取舍** |
| 6.12 | Runtime API 审批通道 | 机器人接进来跑任务 | `test_runtime.py` | 实测：write_file 静默落盘、无审计 | ⬜ **缺陷** |

## 七、记忆与上下文

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 7.1 | 静态记忆（AGENTS.md/NEXUS.md） | 项目约定写进文件 | `test_prompt.py` | 未跑 | 🟡 |
| 7.2 | 动态长期记忆（SQLite） | "记住我用 uv 不用 pip" | `test_memory.py` | 未跑 | 🟡 |
| 7.3 | 相关性召回 Top-K | 不问也能想起相关约束 | `test_memory.py` | 未跑 | 🟡 |
| 7.4 | 自动压缩（80% 触发） | 长会话不炸 | `test_context.py` | 未跑 | 🟡 |
| 7.5 | `/compact` 手动压缩 | 主动瘦身 | `test_context.py` | pty 实测：越压越大 | ⬜ **缺陷** |
| 7.6 | 代码索引与检索 | "搜一下这个函数在哪" | `test_search_rank.py` | 未跑 | 🟡 |

## 八、会话与持久化

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 8.1 | 会话转录 JSONL | 关掉再开还在 | `test_session_store.py` | 实测落盘 | ✅ |
| 8.2 | usage 随转录持久化 | 恢复后 token 接着算 | `test_session_usage.py` | 实测双口径正确 | ✅ |
| 8.3 | `/fork` 分叉 | 试另一个方向不污染主线 | 无 | 未跑 | ⬜ |
| 8.4 | 快照 / `/snapshot` / `/restore` | 改坏了回滚 | `test_snapshot.py` | 未跑 | 🟡 |
| 8.5 | vfs 只读视图 | 对比改前改后 | `test_vfs.py` | 未跑 | 🟡 |
| 8.6 | 自动快照（pre/post turn） | README 说默认开 | 无 | 实测默认关，文档不符 | ⬜ **文档缺陷** |

## 九、联网

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 9.1 | `web_fetch` 抓正文 | "看看这个链接讲了啥" | `test_web_fetch_policy.py` | 实测 GitHub/python.org 取回正文 | ✅ |
| 9.2 | SSRF 守卫 | 别让它抓内网 | `test_web_fetch_policy.py` | 实测 loopback/RFC1918/metadata 被拒 | ✅ |
| 9.3 | `web_search` | 搜最新报错 | 1 | 未跑；失败只回空错误 | 🟡 **缺陷** |
| 9.4 | 系统代理环境 | 公司网/梯子下能用 | `test_proxy_bypass.py` | 实测回环旁路正确 | ✅ |

## 十、MCP

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 10.1 | MCP client（stdio / HTTP） | 接 filesystem server | `test_mcp.py` | 实测 npx server 可接入 | ✅ |
| 10.2 | 工具级过滤 allowed/blocked | 只放几个工具进来 | `test_mcp_filter.py` | 未跑 | 🟡 |
| 10.3 | OAuth 令牌落盘 0600 | 接需要授权的 server | `test_mcp_oauth.py` | 未跑 | 🟡 |
| 10.4 | 自身作为 MCP server | 让别的客户端用 NexusCLI | `test_mcp_server_protocol.py` | 官方 SDK 握手通过（本轮修复） | ✅ |
| 10.5 | MCP server 浏览器安全（Origin/CORS） | 本机跑着别被网页打 | 无 | 实测任意 Origin 都能 POST 跑命令 | ⬜ **缺陷** |
| 10.6 | MCP client 连接复用 | 有状态 server 要保活 | 无 | 实测每次调用新起进程 | ⬜ **缺陷** |
| 10.7 | `mcp list` / `init-chrome` | 看配了啥 | 0 | 未跑 | ⬜ |

## 十一、Runtime API / cron / eval / SDK

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 11.1 | `serve --http` + API key | 机器人接进来 | `test_runtime.py` | 实测 200/401 正确 | ✅ |
| 11.2 | `worker` 消费任务 | 后台跑长任务 | `test_runtime.py` | 实测同项目 14s 完成 | ✅ |
| 11.3 | 任务租约与取消 | 崩溃后自动重排 | `test_runtime.py` | 实测过期租约重排成功 | ✅ |
| 11.4 | Runtime 并发 turn | 同时发两个请求 | 无 | 实测事件流交错 | ⬜ **缺陷** |
| 11.5 | 不存在的 thread | 请求打错 id | 无 | 实测返回 200 并写消息 | ⬜ **缺陷** |
| 11.6 | `cron list/next/run` | 定时跑日报 | `test_cron.py` | 实测 `cron run` 输出不能直接 pipe 给 `-p` | 🟡 **缺陷** |
| 11.7 | `eval list/run` | 改坏了看回归 | `test_eval.py`（30 项） | 实测 3/3 通过 0.43s | ✅ |
| 11.8 | eval 在 wheel 安装形态 | `pip install` 后跑 eval | 无 | 实测无内置场景，exit 1 | ⬜ **缺陷** |
| 11.9 | SDK `create_default_engine` | 在 Python 里调 | 无 | 未跑 | ⬜ |

## 十二、扩展机制

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 12.1 | Skill 系统（builtin/user/project） | 装个团队规范 | `test_skill.py` | 未跑 | 🟡 |
| 12.2 | Agent Skills 规范兼容 | 直接用社区 skill | `test_skill_spec_compat.py` | 未跑 | 🟡 |
| 12.3 | 自定义斜杠命令 | 把常用提示词存起来 | `test_slash_commands.py` | 未跑 | 🟡 |
| 12.4 | 插件（`.nexuscli/plugins/`） | 打包一组 agents+skills+commands | `test_plugins.py` | 未跑 | 🟡 |
| 12.5 | 自定义子代理（`.nexuscli/agents/*.md`） | 定义专属 reviewer | `test_subagent.py` | 未跑 | 🟡 |
| 12.6 | 动态工作流（JSON DAG） | 把固定流程存成文件 | `test_workflow.py` | 未跑 | 🟡 |
| 12.7 | 子代理文件邮箱 | 两个子代理协作 | `test_subagent_mailbox.py` | 未跑 | 🟡 |
| 12.8 | 后台子代理 + 完成播报 | 派出去就不管了 | `test_bg_subagent.py` | 未跑 | 🟡 |

## 十三、可观测性

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 13.1 | `trace_id` 关联 | 把一次请求的所有日志串起来 | `test_trace_id.py` | 未跑 | 🟡 |
| 13.2 | OTel span | 接自己的 collector | `test_telemetry.py` | 未跑（无 exporter，如实边界） | 🟡 |
| 13.3 | `llm.debug_dump` | 排查请求长什么样 | `test_llm_io_dump.py` | 未跑 | 🟡 |
| 13.4 | `/usage stats` 落库 | 看这周花了多少 | `test_usage_coverage.py` | 未跑 | 🟡 |

## 十四、图片与 PDF

| # | 功能 | 真人会怎么用 | 机器验证 | 真实运行 | 状态 |
|---|---|---|---|---|---|
| 14.1 | `@image:` 本地图片 | 贴截图让它看 | `test_image.py`（2 项） | 未跑 | 🟡 |
| 14.2 | 远程图片 | 贴 URL | `test_image.py` | 未跑 | 🟡 |
| 14.3 | 非多模态模型降级 | 用 deepseek 时别报错 | `test_image.py` | 未跑 | 🟡 |
| 14.4 | PDF 读取 | 让它读需求文档 | `test_file_read_pdf.py` | 未跑 | 🟡 |
| 14.5 | Python 语法诊断 | 写完自动查错 | `test_lsp.py`（1 项） | 未跑 | 🟡 |

---

## 十五、汇总

| 状态 | 数量 | 含义 |
|---|---|---|
| ✅ 已测 | 47 | 机器验证 + 真实运行都有 |
| 🟠 仅真人 | 14 | 真实跑过，但没有自动化断言护着（含 4 条缺陷） |
| 🟡 仅机器 | 50 | 有断言，从没在真实场景里跑过（含 5 条缺陷） |
| ⬜ 未测 | 30 | 两条都没有（含 12 条缺陷） |
| **合计** | **141** | 21 条已确认缺陷分布在其中 |

### 最该补的三类

1. **只有机器断言的 50 项**：断言证明代码路径通，不证明用户看到的对。优先补 `/model`、`/task`、`/snapshot`、`/restore`、`/fork` 这些高频但零真实运行的命令。
2. **只有真人跑过的 14 项**：没有回归保护，下次重构会静默退化。`doctor`、`/context`、`/tools`、`/audit` 都该有断言。
3. **21 条已确认缺陷**：12 条完全没有验证、4 条只有真人跑过、5 条只有机器断言——每条的复现命令在 2026-10-18 审计报告（`.spec/specs/2026-10-18_fix-real-usage-gaps/audit-report.md`）里。

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
