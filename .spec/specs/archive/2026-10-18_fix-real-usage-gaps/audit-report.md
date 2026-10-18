# NexusCLI 真实使用场景审计报告（2026-10-18）

> 基线：main@26bea2e。审计方式：三个只读子代理各自在 /tmp/nexus-audit 下用真实 pty / 真实网关 / 真实 MCP 客户端跑日常场景，主线程逐条复核并落地修复。
> 证据：/tmp/nexus-audit/lane-repl.md（855 行）、lane-tools.md（912 行）、lane-platform.md。

## 一、这轮真正修掉的（每条都有回归测试与可复现命令）

| # | 缺陷 | 严重度 | 证据 | 修复 |
|---|---|---|---|---|
| 1 | 回合进行中按一次 Ctrl+C 直接杀掉 REPL（exit 130），本轮不落 transcript | P0 | pty 5/5 复现，pristine HEAD 3/3 | repl.py：loop.add_signal_handler + task.cancel + uncancel；修复后 pty 实测 alive=True、提示 Interrupted、/exit 仍返回 0 |
| 2 | 工具审批提示无法退出：Esc 无效，误输一个字符进入无限重问，第二次 Ctrl+C 才杀进程 | P0 | pty：输入 /usage 后只有 Please select one of the available options | 新增 approval_keys.py 单一映射；Esc/Ctrl+C/Ctrl+D → deny；非法输入提示一次后重问 |
| 3 | write_file / edit_file / save_memory 任何模式都不弹审批，静默覆盖文件 | P0 | 真实注册表 requires_approval=False；pty APPROVAL_PROMPT_COUNT: 0 | builtins.py 补 requires_approval=True；矩阵回归测试锁死 |
| 4 | 只读 bash 直通绕过 path_guard：head -2 ~/.ssh/id_rsa 免审批读出私钥 | P0 | read_file 同路径被拒，bash 返回 BEGIN OPENSSH PRIVATE KEY | bash_readonly.py 增加凭证路径判定，命中即回落审批链 |
| 5 | 助手正文里的单个换行被折叠成空格：模型发 205 行，屏幕 1 段 | P1 | 渲染层最小复现 a/b/c → "a b c" | rich_renderer.py 增加 _preserve_line_breaks（CommonMark 硬换行，跳过代码块/列表/表格/标题） |
| 6 | -p 拒绝信息拼错（denyed/skiped）且不告诉用户怎么放行 | P1 | 真实 -p 输出 | executor.py _denial_message：正确过去式 + 非交互模式点名 permissions.allow / REPL |
| 7 | pytest 向真实 ~/.nexuscli/audit.jsonl 写测试记录 | P1 | 累计 144 条 pytest-of-* | conftest.py autouse _isolate_user_home；复测 delta=0 |
| 8 | -p --mode plan 不开启只读闸门，照常写盘 | P1 | 实测 plan_wrote.txt 落盘 | cli.py 依据 mode 设 policy.plan_mode；复测写入被拒 |
| 9 | MCP server initialize 不符合规范，官方 SDK 客户端握手失败 | P0 | 官方 client：3 个 pydantic validation errors | mcp/server.py 返回 protocolVersion/capabilities/serverInfo.version；官方 client 实测 2024-11-05 nexuscli 0.1.0、25 工具 |
| 10 | web_fetch 把代理 fake-IP 段（198.18.0.0/15）判为内网，联网工具本机 100% 不可用 | P0 | example.com → 198.18.0.38，is_private True | fetch.py 精确化拒绝类（loopback/link-local/multicast/reserved/unspecified/RFC1918/ULA），fake-IP 放行 |
| 11 | /effort 在 deepseek/glm 上显示已设置，但字段根本不会发送 | P2 | 真实 REPL 输出三行绿色成功 | effort.py 增加 effort_reaches_wire，非白名单 provider 明确打印 Not sent |

## 二、发现了但本轮没修的（如实挂账，附复现入口）

| # | 缺陷 | 严重度 | 为什么挂账 |
|---|---|---|---|
| 1 | -p 模式没有审批回调，需要审批的工具一律被拒（自动化场景没有 shell） | P0（设计取舍） | 已写进 README/docs 作为如实边界，并验证 permissions.allow 可放行；改成默认放行需要产品决策 |
| 2 | 只读直通仍可读工作区内任意文件（含用户自己放的密钥） | P2 | 凭证类已挡；工作区内文件属用户责任 |
| 3 | --mode plan 与 REPL plan 语义已对齐，但 team 模式的 worker 未纳入只读闸门 | P2 | 需设计 team 的只读语义 |
| 4 | task_stop 只杀直接子进程，孙进程继续跑；bash timeout 被孙进程持管道拖长（1s 超时实测 12s） | P1 | 需进程组改造（start_new_session + killpg），跨平台风险高 |
| 5 | 审计日志原样落盘 write_file 全文与 bash 命令（0644） | P1 | 需要脱敏口径设计（哪些字段算正文） |
| 6 | 窄终端（≤60 列）状态栏裁掉权限模式名，Shift+Tab 无可见反馈 | P1 | 需要状态栏降级布局设计 |
| 7 | 斜杠命令 Tab 补全失效（/ 是 prompt_toolkit 词分隔符） | P1 | 需要自定义 completer |
| 8 | placeholder 写 @path/to/file，真实语法是 @image: | P2 | 文案层，随下一轮观感 |
| 9 | /compact 越压越大且标签显示成功 | P1 | 需要复现压缩预算逻辑 |
| 10 | MCP 客户端每次工具调用新起 server 进程（0.8s 起，10 次里 3 次 62s 超时） | P1 | 需要长驻连接池设计 |
| 11 | Runtime API 任务消费没有审批通道 | P1 | 与 -p 同源，随审批产品决策 |

## 三、验证基线

- .venv/bin/python -m pytest → 889 passed, 3 skipped
- .venv/bin/python -m ruff check src tests → All checks passed
- .venv/bin/python -m ruff format --check src tests → 199 files already formatted
- 真实网关冒烟：-p 单发（含工具调用）/ plan / team 三模式均跑通
- 官方 MCP 客户端握手通过；web_fetch 对 GitHub / python.org 取回正文（example.com 因本机代理不稳定偶发 ConnectError，属环境）
