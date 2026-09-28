# 工程蒸馏：Agent 平台能力轮（2026-09-28）

本轮为 nexusCLI 补齐六项平台能力（权限规则/hooks/子代理 task/斜杠命令增强//compact/plan 模式）过程中沉淀的项目专属工程事实。

## win32 路径与环境

- **`Path.home()` 在 win32 优先读 `USERPROFILE`，`HOME` 只有在 USERPROFILE 未设时才生效**。任何依赖家目录配置隔离的测试必须同时 monkeypatch `HOME` 与 `USERPROFILE`（实证：`tests/test_permissions.py` 的分层合并用例曾因此 fail-open 成空断言）。不要为"修测试"把 `config._home()` 改成优先读 `HOME`——Git Bash 会把 `/c/...` 风格路径灌进生产配置解析。
- 子进程在 Git Bash 里执行 `> nul` 类重定向会真在仓库根创建名为 `nul` 的文件（Windows 保留设备名）；清理用 `cmd //c "del \\\\.\\<绝对路径>\\nul"`。

## Mimosa 预写钩子约束下的子进程执行

- **`subprocess.run(cmd, shell=True)` 形态会被 Mimosa 预写钩子按"高危·命令注入"拦截，无豁免写法**。仓库认可的执行通道是 `tools/commands.py` 的 `CommandExecutor`（底层 `asyncio.create_subprocess_shell`，与 `tools/builtins.py` 的 bash 工具同款）：它同时提供 CommandGuard 黑名单、timeout、输出截断。任何新功能要跑 shell 命令（如斜杠命令的 `` !`cmd` `` 注入）都应复用它并显式传 `CommandGuard(list(config.policy.command_blacklist))`，否则会形成比 bash 工具更宽的旁路。
- 测试的 async 代码用同步 `def test_*` + `asyncio.run(...)` 的仓库习语，不引入 pytest-asyncio。

## 配置分层合并的列表语义

- `config._deep_merge` 对 list 是**整体替换**。凡语义上需要"跨层累加"的列表（permissions.allow/deny/ask、五事件 hooks）必须有专门的拼接函数（参见 `_merge_permission_lists`、`_merge_hook_lists`）：user 在前、project 在后、去重保序。
- 运行时会话/策略状态写在 `config.policy` 上是既定模式（`hitl_mode` 被 PermissionModeController 实时改写先例）：`plan_mode`、`session_id` 沿用。共享 config 对象上的会话戳只允许"为空才写"（子代理防覆盖，见 agent.py session_id 注释）。

## 并行车道的文件所有权纪律

- 并行实现车道必须**文件集两两不相交**；跨车道句柄传递用两种方式落地：经 `config.policy` 运行时字段（hooks 的 session_id），或子代理自建 `create_llm_client(config.llm)` + 基于 `get_builtin_tools()` 的过滤注册表（不碰主 Agent 文件）。
- 车道间发现他人文件缺陷时：先重读实时文件（对方可能已自修复），确认仍存在则升级给所有者裁定，禁止并行写同一文件。

## 测试与门禁

- pyproject 已有 `addopts = "-q"`：命令行再传 `-q` 会变成 `-qq`，**pytest 汇总行被吞**，验收时只传一次（或不传）。
- 本轮基线：全量 `uv run python -m pytest` 226 passed；`ruff check` + `ruff format --check` 干净。安全评审六项发现（hooks timeout null 兜底、子代理深度透传、auto 态 ask 强制审批、注入黑名单同源、session_id 防覆盖、web_search 域规则不误匹配 query）均已修复并有探针级用例。
