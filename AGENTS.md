# AGENTS.md

面向在本仓库工作的 coding agent。这里只放从已归档 spec 包的「AGENTS.md 增量提议」与
工程事实里蒸馏出的守则——每条都注明来源包,且不重复 README.md / NEXUS.md 已有内容
(定位、命令、架构、修改联动规则见 NEXUS.md;优先级:NEXUS.md「信息优先级」一节,
本文件居代码与测试之后、NEXUS.md 之前)。

## 测试与隔离

- pytest 全程重定向 HOME **与** USERPROFILE 两个环境变量(win32 的 `Path.home()` 偏好
  USERPROFILE,只重定向 HOME 会从那边泄漏),否则审计日志、会话、用量库会被测试写进
  真实用户目录。已固化为 `tests/conftest.py` 的 autouse fixture,新增测试不要绕开它。
  ——来源:2026-10-18_fix-audit-residual 增量提议;现状 `tests/conftest.py:19-36`
- pty 测试必须自建 pty 并应答光标位置查询 `ESC[6n`(CPR);裸 pexpect 会整屏错位,
  跑出来的"通过"是假结论。——来源:2026-10-18_fix-real-usage-gaps 增量提议

## 进程与信号

- 后台任务与 bash 统一 `start_new_session` + `killpg`;停止必须等进程组真正清空才报
  stopped,否则孙进程还在跑却谎报成功。——来源:2026-10-18_fix-audit-residual 增量提议
- prompt_toolkit 在 `prompt_async()` 返回时会 `remove_signal_handler`,SIGINT 回落
  `SIG_DFL`;长任务期间必须重新 `add_signal_handler` + `task.cancel`,直接
  `raise KeyboardInterrupt` 会被 `asyncio.run` 转成 `SystemExit(130)`。
  ——来源:2026-10-18_fix-real-usage-gaps 增量提议

## 审批与写域

- 任何需要人点头的工具都必须 `requires_approval=True`;`-p` 无审批回调时按失败关闭,
  放行必须走显式的 `--yes`。——来源:2026-10-18_fix-audit-residual 增量提议
- 审批提示必须带原因行(`Reason: <description>`):堆叠的审批框除危险级别外长得一样,
  用户分不清是哪个门在问。——来源:2026-10-18_verify-zero-and-yellow 缺陷修复
  「审批无原因行」;现状 `src/nexuscli/entrypoints/commands/approval.py:59`
- 写域守卫的 allow 语义:显式 `allow` 规则是用户的长期信任决定(执行器链视为
  auto-approve),内层写域提示不得对同一调用重复问;`hitl_mode == "always"` 例外;
  `approve_run` 只确认单条命令的写域,不外溢到整个 run;`2>/dev/null` 是标准丢弃目标,
  不算域外写。——来源:2026-10-18_verify-zero-and-yellow 缺陷修复「写域守卫无视
  allow + 误报 /dev/null」;现状 `src/nexuscli/tools/builtins.py:627-660`
- 判断只读命令不能只看命令名:`head`/`cat` 指向 `~/.ssh`、`~/.nexuscli`、`.env`、
  `*.pem` 时等同外带凭证,必须并入审批链;只读 bash 直通不能替代路径守卫。
  ——来源:2026-10-18_fix-real-usage-gaps + 2026-10-18_fix-audit-residual 增量提议

## MCP

- server 的 initialize 必须返回 `protocolVersion` / `capabilities` /
  `serverInfo.version`,否则官方 SDK 全部握手失败;curl 能过不代表官方 SDK 客户端能过。
  ——来源:2026-10-18_fix-audit-residual + 2026-10-18_fix-real-usage-gaps 增量提议
- client 会话要复用:每次新起子进程会丢服务端状态(有状态 server 的同一进程、状态
  累计承诺)。——来源:2026-10-18_fix-audit-residual 增量提议

## 渲染与模型网关

- `rich.Markdown` 把单个换行当软换行折叠成空格:助手正文必须补 CommonMark 硬换行,
  否则多行输出在终端里塌成一段;`-p` 路径不受此影响,容易漏测。
  ——来源:2026-10-18_fix-audit-residual + 2026-10-18_fix-real-usage-gaps 增量提议
- 压缩/裁剪摘要必须放 **user** 角色:严格交替网关会 outright 拒绝 assistant 开头或
  assistant 连续的请求(GLM 实测 HTTP 400 code 11133);真实网关验证 assistant 角色
  摘要 → 400,user 角色 → 通过。——来源:2026-10-18_verify-zero-and-yellow 缺陷修复
  「压缩摘要角色」;现状 `src/nexuscli/context/manager.py`
- 视觉能力按能力字段判定,不按模型名:`ModelProfile.vision` 是三态能力开关
  (来自网关 listing 的 `capabilities.vision`),`None` 才回落名字启发式;按
  `supports_images` 之类名字门槛会误判。——来源:2026-10-18_verify-zero-and-yellow
  缺陷修复「supports_images 名称门槛」;现状 `src/nexuscli/llm/model_profiles.py:24-26`
