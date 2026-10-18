# NexusCLI 真实使用场景审计与缺口修复 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁

## 阶段一：真实使用审计
- [x] 交互 REPL / TUI 真实场景审计（pty + CPR 应答 + pyte 屏幕仿真：首帧、slash、Shift+Tab、Ctrl+C、窄宽终端、非 TTY）
  - boundary: 只读审计，不改动仓库文件；临时文件放 /tmp/nexus-audit
  - verify: manual: 报告 /tmp/nexus-audit/lane-repl.md（855 行，17 条）；主线程逐条复核关键项并端到端复现
- [x] 工具面 / 权限 / 策略 / 持久化真实场景审计
  - boundary: 只读审计
  - verify: manual: 报告 /tmp/nexus-audit/lane-tools.md（912 行、16 条）；主线程复核 P0 三条并复现
- [x] 联网 / MCP / Runtime API / cron / eval / SDK 真实场景审计
  - boundary: 只读审计；允许在 /tmp 起临时服务
  - verify: manual: 报告 /tmp/nexus-audit/lane-platform.md（15 条）；主线程复核 F1/F3 并修复后复测

## 阶段二：修复（本轮已落地，每条都有回归测试）
- [x] F1 非交互 `-p` 审批拒绝提示语：修正 `denyed`/`skiped` 拼写，并指明 `permissions.allow` / REPL 两条放行途径
  - boundary: src/nexuscli/tools/executor.py 的拒绝信息与其断言
  - verify: `.venv/bin/python -m pytest -q tests/test_permissions.py`
- [x] F2 测试隔离：pytest 不再写真实 `~/.nexuscli/audit.jsonl`
  - boundary: tests/conftest.py 的 autouse fixture
  - verify: `.venv/bin/python -m pytest -q tests/test_permissions.py tests/test_hooks.py`
- [x] F3 文档口径对齐：README 与 docs/permissions.md 增加「单次 -p 没有审批弹窗」的如实边界
  - boundary: README.md、docs/permissions.md、site/docs/permissions.html（重新构建）
  - verify: manual: 主线程对照真实 `-p` 行为逐条核对（已用 isolated config 实测 allow 规则放行）
- [x] F4 `/effort` 诚实性：非白名单 provider 不再假装档位生效
  - boundary: src/nexuscli/entrypoints/commands/effort.py
  - verify: `.venv/bin/python -m pytest -q tests/test_effort_switch.py`
- [x] F5 REPL Ctrl+C 在回合中途不再杀进程（`add_signal_handler` + `task.cancel`）
  - boundary: src/nexuscli/entrypoints/repl.py
  - verify: `.venv/bin/python -m pytest -q tests/test_repl.py`
- [x] F6 审批提示可退出：Esc/Ctrl+C/Ctrl+D 视为 deny，非法输入只提示一次
  - boundary: src/nexuscli/entrypoints/commands/approval.py、新增 approval_keys.py
  - verify: `.venv/bin/python -m pytest -q tests/test_approval_keys.py`
- [x] F7 助手正文单换行不再被折叠成一段
  - boundary: src/nexuscli/render/rich_renderer.py
  - verify: `.venv/bin/python -m pytest -q tests/test_render.py`
- [x] F8 写盘类工具必须审批：`write_file` / `edit_file` / `save_memory` 补 `requires_approval`
  - boundary: src/nexuscli/tools/builtins.py
  - verify: `.venv/bin/python -m pytest -q tests/test_tool_approval_matrix.py`
- [x] F9 只读直通不再外带凭证文件（`~/.ssh/id_rsa`、`~/.nexuscli/config.json`、`.env` 等）
  - boundary: src/nexuscli/policy/bash_readonly.py
  - verify: `.venv/bin/python -m pytest -q tests/test_bash_readonly.py`
- [x] F10 `-p --mode plan` 真正开启只读闸门
  - boundary: src/nexuscli/entrypoints/cli.py
  - verify: `.venv/bin/python -m pytest -q tests/test_cli.py`
- [x] F11 MCP server initialize 返回规范形状（protocolVersion / capabilities / serverInfo.version）
  - boundary: src/nexuscli/mcp/server.py
  - verify: `.venv/bin/python -m pytest -q tests/test_mcp_server_protocol.py`
- [x] F12 web_fetch 不再把代理 fake-IP（198.18.0.0/15）当内网
  - boundary: src/nexuscli/web/fetch.py
  - verify: `.venv/bin/python -m pytest -q tests/test_web_fetch_policy.py`

## 阶段三：验收
- [x] 全量质量门 + 真实 `-p` 冒烟
  - boundary: 只跑验证，不改代码
  - verify: `.venv/bin/python -m pytest -q` → 889 passed, 3 skipped；ruff check / format 全绿
- [x] 审计结论落成包内文档（缺陷清单 + 处置结果）
  - boundary: 只写 .spec 包内文档
  - verify: manual: audit-report.md 已落盘，每条结论都指向 lane 报告中的真实命令与输出；11 条修复 + 11 条挂账

---

**当前进度**：由脚本计算，无需手动维护
