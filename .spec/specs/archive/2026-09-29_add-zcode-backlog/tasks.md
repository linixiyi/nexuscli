# nexusCLI ZCode 差距存量台账任务包 - 任务拆解

## 使用规则

- 每个任务都要写清楚 `boundary` 和 `verify`
- 如果一个任务没有验证方式，就不能开始
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- 同波任务 boundary 写集两两不相交（各波注记已逐对检查）；共写热点文件（repl.py / config.py / openai_compatible.py / subagent.py / slash_commands.py / pyproject.toml）的任务跨波排布并以 depends-on 串行
- README.md 与既有 docs/*.md 的更新统一归收尾波 task-final-docs-sync；实现任务只写代码、测试与新建文件

## 阶段一：W1 小任务与 S 级 P1（5 任务，写集互不相交，可全并行）

- [x] 补 background stop 升级阶梯（terminate→kill）测试
  - id: task-bgstop-kill-ladder-test
  - boundary: 只新增/修改 `tests/test_background.py`；以注入 fake process（terminate() 不生效、wait() 挂起）+ monkeypatch 缩短 grace 常量的方式触发 kill 分支；不改 `src/nexuscli/tools/background.py` 行为（若确需测试缝隙，只允许把 `_STOP_GRACE_SECONDS` 改为可注入参数且默认值不变）
  - verify: `uv run python -m pytest tests/test_background.py` 全绿，新增用例断言 grace 超时后走到 kill() 且 status 返回 "stopped"，既有用例不回归
  - evidence: 2026-09-29 W1 轮完成：test_background.py 9 passed（8 既有 + 1 新增 kill 阶梯用例），src 零改动（git diff 核实）
- [x] 子代理 Agent 显式传小 max_turns（A3）
  - id: task-subagent-turns-cap
  - boundary: `src/nexuscli/agent/subagent.py`（run_subagent 构造 Agent 时显式传 max_turns，新增模块级常量 `SUBAGENT_MAX_TURNS = 30` 并以注释写明取舍）；`tests/test_subagent.py`（断言子代理 Agent 收到显式 max_turns，不受 config.agent.max_turns=200 影响）
  - verify: `uv run python -m pytest tests/test_subagent.py tests/test_agent_turns.py` 全绿
  - evidence: 2026-09-29 W1 轮完成：22 passed；SUBAGENT_MAX_TURNS=30 于 subagent.py:37、构造实参 :198；README 说明行已由 task-w1-docs-sync 落
- [x] hooks 补 PermissionRequest / PostToolUseFailure 事件（C1）
  - id: task-hooks-new-events
  - boundary: `src/nexuscli/config.py`（HOOK_EVENT_FIELDS 增加 PermissionRequest、PostToolUseFailure 两键）；`src/nexuscli/tools/executor.py`（审批决策处 fire PermissionRequest、工具执行失败分支 fire PostToolUseFailure，payload 含工具名与决策/错误摘要）；`tests/test_hooks.py`（两个新事件的触发与 payload 用例，参照既有 hook 测试构造）；不改 README/docs（归收尾任务）
  - verify: `uv run python -m pytest tests/test_hooks.py tests/test_permissions.py tests/test_plan_mode.py` 全绿（三条拦截线语义不回退）+ `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W1 轮完成：57 passed（含 4 新用例）；验收补救：实现代理报告的边界外缺陷 hooks/registry.py:16 `_TOOL_EVENTS` 未含新事件（非 * matcher 被静默忽略）已由主会话修复（frozenset 补两键 + test_new_tool_events_honor_matcher 用例），test_hooks.py 34 passed、全量 351 passed
- [x] 微压缩 microcompact（C3）
  - id: task-microcompact
  - boundary: `src/nexuscli/context/manager.py`（prepare 触发有损压缩前，先就地清除较旧回合的工具结果载荷——替换为占位符、保留消息结构；新增阈值常量并在模块 docstring 记录取舍）；`tests/test_context.py`（旧工具结果被就地清除、近期回合不动、清除后仍超阈值才走有损压缩）
  - verify: `uv run python -m pytest tests/test_context.py tests/test_query.py` 全绿
  - evidence: 2026-09-29 W1 轮完成：16 passed（10 既有 + 3 新增 + test_query 3）；compressed=False 不误报压缩；compact_now 零改动
- [x] /skill 强制加载（C4）
  - id: task-skill-force-load
  - boundary: `src/nexuscli/entrypoints/repl.py`（/skill 子命令支持强制加载语义：置「下一条 prompt 强制加载」状态，下一次用户输入前拼接 skill body 前缀交给 agent，加载一次即消费）；新建 `tests/test_skill_force_load.py`（stub agent.run 捕获拼接后的 prompt，断言含 skill body 且只影响下一回合）；不改 `src/nexuscli/skill/registry.py` 的加载语义
  - verify: `uv run python -m pytest tests/test_skill_force_load.py tests/test_skill.py tests/test_repl.py` 全绿
  - evidence: 2026-09-29 W1 轮完成：24 passed（3 新增）；经 agent.skill_context_buffer 一次性 drain，registry.py 零改动（git diff 核实）
- [x] W1 文档收口（波内新增收口任务，编排方指令）
  - id: task-w1-docs-sync
  - boundary: 只改 `README.md` 与 `docs/hooks.md`；四个功能点 + A4 说明行同步
  - verify: 锚点/内链核验 0 断链 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W1 轮完成：自写 slug 校验脚本 43 链接 0 断链 PASS；ruff 全绿；主会话复核 README:94/:204/:220-233/:267-270/:332-354 与 docs/hooks.md 落点及 README:94 七事件枚举扩展（合理，避免自相矛盾）

> W1 冲突检查：{tests/test_background.py}、{subagent.py, tests/test_subagent.py}、{config.py, executor.py, tests/test_hooks.py}、{context/manager.py, tests/test_context.py}、{repl.py, tests/test_skill_force_load.py} 两两不相交。

## 阶段二：W2 M 级 P1 与轻量 P2（5 任务，写集互不相交，可全并行）

- [x] 工作区 hooks sha256 信任机制（C2）
  - id: task-hooks-sha256-trust
  - depends-on: task-hooks-new-events, task-skill-force-load
  - boundary: `src/nexuscli/hooks/registry.py` 与新建 `src/nexuscli/hooks/trust.py`（工作区 hooks 配置的 sha256 指纹计算与信任存储 `~/.nexuscli/hooks-trust.json`，不匹配时拒绝执行）；`src/nexuscli/config.py`（信任状态读写接线，如需）；`src/nexuscli/entrypoints/repl.py`（首次遇到未信任工作区 hooks 时一次性确认，拒绝则本次会话禁用工作区 hooks；用户级 hooks 不受影响）；新建 `tests/test_hooks_trust.py`（首次确认→指纹落盘→篡改后拒绝）
  - verify: `uv run python -m pytest tests/test_hooks_trust.py tests/test_hooks.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W2 轮完成：41 passed（7 新用例）；registry.py 零改动；主会话复核 trust.py 为 project-layer 作用域、存储仅指纹无秘密
- [x] PDF 文档读取（C6）
  - id: task-pdf-read
  - boundary: `pyproject.toml`（可选依赖组 `pdf = ["pypdf>=4"]`，并把 pypdf 加入 dev 组使 CI 可测提取路径）；`uv.lock`（`uv lock` 同步产物）；`src/nexuscli/tools/file_ops.py`（read_file 对 .pdf 后缀走 pypdf 提取文本，缺依赖时返回带安装指引的明确错误）；`src/nexuscli/tools/builtins.py`（read_file description 补 PDF 支持一句）；新建 `tests/test_file_read_pdf.py`（tmp 生成 PDF→提取断言；monkeypatch 模拟 pypdf 缺失的错误路径）
  - verify: `uv sync --extra dev --frozen` 退出码 0（lock 一致）+ `uv run python -m pytest tests/test_file_read_pdf.py tests/test_tools.py` 全绿
  - evidence: 2026-09-29 W2 轮完成：6 passed（3 新用例含缺依赖错误路径）；uv lock 同步后主会话复跑 uv sync --extra dev --frozen 退出码 0（Checked 48 packages）；pyproject pdf=["pypdf>=4"] 已核
- [x] 原生 Anthropic 协议客户端（P2，mock 验证）
  - id: task-anthropic-native
  - boundary: 新建 `src/nexuscli/llm/anthropic.py`（Anthropic Messages API 客户端：接口与 OpenAICompatibleClient 对齐——chat 流式/usage/cost，system 与 tools 的 Anthropic 映射；API key 只从环境变量 ANTHROPIC_API_KEY 或既有凭据路径读取，源码与测试一律占位符 `<api-key>`）；`src/nexuscli/llm/factory.py`（provider=anthropic 分派）；新建 `tests/test_anthropic_client.py`（mock httpx 响应断言请求体映射、流式事件转换与 usage 统计，不发真实网络请求）
  - verify: `uv run python -m pytest tests/test_anthropic_client.py tests/test_llm_usage.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W2 轮完成：15 passed（5 新用例）；全 mock 零真实网络；主会话凭据扫描零命中（仅 <api-key> 占位符与 ANTHROPIC_API_KEY 环境变量名）
- [x] MCP OAuth 授权支持（P2，mock 验证）
  - id: task-mcp-oauth
  - boundary: 新建 `src/nexuscli/mcp/auth.py`（OAuth 2.0 授权码 + PKCE 的 token 获取/刷新；token 只写入 `~/.nexuscli/` 凭据文件（0600）或经环境变量注入，源码/测试不出现真实凭据字面量）；`src/nexuscli/mcp/config.py`（McpServerSpec 增加 auth 字段）；`src/nexuscli/mcp/client.py`（http 类 transport 附 Authorization 头，401 时用 refresh_token 重试一次）；新建 `tests/test_mcp_oauth.py`（stub transport / mock httpx 断言 PKCE 参数生成、token 刷新与 401 重试路径，不发真实网络请求）
  - verify: `uv run python -m pytest tests/test_mcp_oauth.py tests/test_mcp.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W2 轮完成：12 passed（9+1 新用例：PKCE/刷新/401 重试/解析）；凭据工厂生成假 token 零字面量；已知取舍（docstring 记录）：_validate_token_url 不做 DNS 解析（保留 TLD 测试需要）——收尾 Mimosa 审计复核该面
- [x] traceId 全链路（P2）
  - id: task-trace-id
  - depends-on: task-hooks-new-events
  - boundary: 新建 `src/nexuscli/observability.py`（会话级 trace id 生成与上下文存取）；`src/nexuscli/tools/executor.py`（审计记录增加 trace_id 字段）；`src/nexuscli/llm/openai_compatible.py`（chat 请求附 X-Request-ID 头）；新建 `tests/test_trace_id.py`（stub client 断言同一会话内审计记录与 LLM 请求头携带同一 trace id）
  - verify: `uv run python -m pytest tests/test_trace_id.py tests/test_permissions.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W2 轮完成：24 passed（5 新用例）；executor 7 处审计调用带 trace_id、LLM 请求附 X-Request-ID；缝隙回写：boundary 实际扩展至 `policy/audit_log.py`（AuditLog.record 加可选 trace_id 参数、默认空、向后兼容）——W2 简报预判并授权的最小缝隙

> W2 冲突检查：{hooks/registry.py, hooks/trust.py, config.py, repl.py, tests/test_hooks_trust.py}、{pyproject.toml, uv.lock, file_ops.py, builtins.py, tests/test_file_read_pdf.py}、{llm/anthropic.py, llm/factory.py, tests/test_anthropic_client.py}、{mcp/auth.py, mcp/config.py, mcp/client.py, tests/test_mcp_oauth.py}、{observability.py, executor.py, openai_compatible.py, tests/test_trace_id.py} 两两不相交。

## 阶段三：W3 两个 L 级 P1 主体（2 任务，写集互不相交）

- [x] 插件系统·本地目录 MVP（C7，L）
  - id: task-plugin-local-mvp
  - depends-on: task-subagent-turns-cap
  - boundary: 新建 `src/nexuscli/plugins/`（loader.py：发现 `.nexuscli/plugins/<name>/plugin.json` manifest 与 agents/、skills/、commands/ 子目录，manifest 损坏安全跳过）；`src/nexuscli/bootstrap.py`（启动装配插件目录）；`src/nexuscli/agent/subagent.py`（load_subagents 接受插件 agents 目录并入）；`src/nexuscli/skill/registry.py`（插件 skills 目录并入）；`src/nexuscli/entrypoints/slash_commands.py`（插件 commands 目录并入自定义命令查找）；新建 `tests/test_plugins.py`（tmp 造含 agents/skills/commands 的插件断言三类注册表并入；坏 manifest 跳过）
  - verify: `uv run python -m pytest tests/test_plugins.py tests/test_subagent.py tests/test_skill.py tests/test_slash_commands.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W3 轮完成：48 passed（10 新用例：三类注册表并入、坏/非法字节 manifest 安全跳过）；缝隙定案：bootstrap.py 为扩展既有文件（非新建，函数体首行 eager load_plugins）；主会话复核 loader 的 OSError+UnicodeDecodeError 双捕获与 warnings.warn 前缀
- [x] 后台子代理与完成通知（P2，复用既有 background 基建）
  - id: task-bg-subagent-notify
  - depends-on: task-hooks-sha256-trust, task-pdf-read
  - boundary: `src/nexuscli/tools/builtins.py`（task 工具增加 run_in_background 参数：立即返回 task_id 不阻塞回合）；新建 `src/nexuscli/agent/bg_tasks.py`（子代理后台任务注册表：asyncio.create_task 包装 run_subagent，完成态/结果缓存与报告文件落盘）；`src/nexuscli/tools/background.py`（如需类型泛化仅做最小改动，不改既有 bash 后台行为）；`src/nexuscli/entrypoints/repl.py`（回合结束后输出已完成后台子代理的摘要通知，非阻塞）；新建 `tests/test_bg_subagent.py`（stub 子代理：立即返回、完成通知、结果可读）
  - verify: `uv run python -m pytest tests/test_bg_subagent.py tests/test_background.py tests/test_subagent.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W3 轮完成：28 passed（8 新用例）；主会话复核 background.py 零改动（git diff 空）、repl.py:267/:289 两处回合末播报；缝隙定案：builtins._task_output 读路径接线为验收必需；Mimosa 对 .execute({task_id}) 形态误报 SQL 注入，测试改用 registry.get + handler 直调（与 test_background.py:300 既有模式一致），未绕过扫描器

> W3 冲突检查：{plugins/, bootstrap.py, subagent.py, skill/registry.py, slash_commands.py, tests/test_plugins.py} 与 {builtins.py, agent/bg_tasks.py, background.py, repl.py, tests/test_bg_subagent.py} 不相交。

## 阶段四：W4 动态工作流与子代理增强（3 任务，后两个串行）

- [x] 动态工作流·JSON 步骤图最小切片 + /expert 入口（C8 + P2 /expert 合并，L）
  - id: task-workflow-engine
  - depends-on: task-plugin-local-mvp, task-bg-subagent-notify
  - boundary: 新建 `src/nexuscli/workflow/`（graph.py：JSON 步骤图 schema——steps[{id, prompt, depends_on}] 的加载/校验/就绪步计算与无环校验，DAG 语义对齐 plan/models.py 风格）；`src/nexuscli/entrypoints/slash_commands.py`（/workflow run <file>.json 展开为受控编排提示词）；`src/nexuscli/entrypoints/repl.py`（/workflow 与 /expert <topic> 分支：按拓扑序串行派发子代理并汇收报告——最小切片不做并行执行器）；新建 `tests/test_workflow.py`（JSON 图加载/校验/拓扑序、环检测拒绝、/expert 展开提示词含步骤）
  - verify: `uv run python -m pytest tests/test_workflow.py tests/test_plan.py tests/test_slash_commands.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W4 轮完成：46 passed；环检测拒绝有 1/2/3 节点三形态用例（tests/test_workflow.py:84-95，主会话复核）；/workflow 与 /expert 共用 _run_workflow_graph 串行派发；最小切片不做并行执行器已入模块注释
- [x] 子代理持久记忆目录（P2）
  - id: task-subagent-memory
  - depends-on: task-plugin-local-mvp
  - boundary: `src/nexuscli/agent/subagent.py`（子代理 system prompt 追加 per-agent 记忆目录约定 `~/.nexuscli/agent-memory/<agent-name>/`：跨委派保留的读写备忘）；`src/nexuscli/memory/manager.py`（如需 scoped 读写 API 只做最小扩展）；`tests/test_subagent.py`（断言子代理 prompt 含记忆目录段且目录按 agent 名隔离）
  - verify: `uv run python -m pytest tests/test_subagent.py tests/test_memory.py` 全绿
  - evidence: 2026-09-29 W4 轮完成：24 passed（5 新用例）；memory/manager.py 零改动（git diff 空，主会话复核）；编排裁决②已执行：提示词带 path_guard 降级文案（subagent.py:178/:228）——真实可用性限制待 W7 文档如实披露
- [x] 子代理间通信原语（P2）
  - id: task-subagent-channel
  - depends-on: task-subagent-memory
  - boundary: 新建 `src/nexuscli/agent/mailbox.py`（文件邮箱 `.nexuscli/mailbox/<run>/` 每代理收件箱，JSONL 追加写）；`src/nexuscli/agent/subagent.py`（build_subagent_registry 注册 mailbox_post / mailbox_read 两工具——仅子代理注册表注册）；新建 `tests/test_subagent_mailbox.py`（两假代理互发消息、跨 run 隔离、工具仅出现在子代理注册表）
  - verify: `uv run python -m pytest tests/test_subagent_mailbox.py tests/test_subagent.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W4 轮完成：22 passed（6 新用例：互发/跨 run 隔离/仅子代理注册白名单回归线）；mailbox run-id 钉 session_id 或 default（裁决③）；Mimosa 对 .execute({...}) 形态再次误报，测试已按钩子要求改为 helper 变量传参写法，断言原样

> W4 冲突检查：workflow{workflow/, slash_commands.py, repl.py} 与 subagent-memory{subagent.py, memory/manager.py, tests/test_subagent.py} 不相交；subagent-channel 与 subagent-memory 共写 subagent.py → 已加 depends-on 串行。

## 阶段五：W5 遥测/IO/工具类 P2（4 任务，前两个串行）

- [x] OpenTelemetry 遥测（P2，mock 验证）
  - id: task-otel-telemetry
  - depends-on: task-trace-id, task-hooks-sha256-trust, task-pdf-read
  - boundary: 新建 `src/nexuscli/telemetry/`（可选依赖 opentelemetry-api/sdk，未安装时 no-op）；`src/nexuscli/config.py`（telemetry.enabled 默认 false）；`src/nexuscli/llm/openai_compatible.py` 与 `src/nexuscli/tools/executor.py`（llm.chat / tool.call 两类 span，属性含 trace_id）；`pyproject.toml`（可选依赖组 telemetry + dev 组加 SDK）；`uv.lock`（uv lock 同步）；新建 `tests/test_telemetry.py`（in-memory span exporter 断言 span 与属性；monkeypatch import 模拟 SDK 缺失时 no-op）
  - verify: `uv sync --extra dev --frozen` 退出码 0 + `uv run python -m pytest tests/test_telemetry.py tests/test_llm_usage.py` 全绿
  - evidence: 2026-09-29 W5 轮完成：17 passed（7 新用例）+ sync 0（主会话复跑 Checked 51 packages）；出入⑧回写：factory 签名为 LlmConfig，简报的 ×4 一行缝隙必 AttributeError（实测复现），经 escalate 裁决为方案 B——工厂加 telemetry_enabled 关键字参数（默认 False）+ 7 处生产调用点接线（repl×2/cli/runtime×2/subagent/sdk，主会话 grep 逐一核实 7/7），5 个 boundary 外文件入列；防回退测试 test_factory_wiring_produces_chat_span（tests/test_telemetry.py:223）；--version/--help 实测通过；record_outcome 在 with 块内（ended-span 陷阱规避）
- [x] 模型 IO 调试落盘（P2）
  - id: task-llm-io-dump
  - depends-on: task-otel-telemetry
  - boundary: `src/nexuscli/config.py`（LlmConfig 增加 debug_dump: bool = False）；`src/nexuscli/llm/openai_compatible.py`（开启时每回合请求/响应 JSON 追加写入 `~/.nexuscli/debug/llm/<date>.jsonl`，Authorization 等敏感头脱敏为占位符）；新建 `tests/test_llm_io_dump.py`（stub httpx：断言落盘结构、敏感字段脱敏、默认关闭不写文件）
  - verify: `uv run python -m pytest tests/test_llm_io_dump.py tests/test_config.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W5 轮完成：8 passed（6 新用例）；_redact_headers + <redacted> 占位符（主会话复核 openai_compatible.py:320-328）；默认关闭不写文件有测试；假凭据走环境变量回退占位符（Mimosa 拦截后按仓库既定 _placeholder 口径改写）
- [x] 嵌入式 rg 搜索加速（P2）
  - id: task-embedded-rg
  - depends-on: task-pdf-read
  - boundary: `src/nexuscli/tools/file_ops.py`（grep 先以 shutil.which 探测 PATH 上的 rg，命中则子进程调用并映射回既有结果结构；未命中或 rg 失败回退纯 Python 路径，语义与现有 grep 用例一致）；`tests/test_tools.py`（新增用例：monkeypatch which 返回 None 断言回退；rg 存在路径在有 rg 的机器上跑、无 rg 时 skipif）
  - verify: `uv run python -m pytest tests/test_tools.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W5 轮完成：6 passed（3 新用例：回退/真 rg 双路一致/rg 失败回退）；出入⑥回写：tasks.md 原「语义与现有 grep 用例一致」的既有用例引用悬空（tests 无直接 grep 语义用例），由本任务新用例补位钉死；实现者实测抓到简报命令错误：裸 !dir/** 位置参数被 rg 当搜索路径（退出码 2、快速路永久死路），改为 --glob !dir/** 逐个传递（file_ops.py:434-447，主会话复核）+ Popen 加 utf-8/replace 防 cp936 解码崩溃——两处修正均有探针实测记录
- [x] Cron 定时任务（P2，离线形态）
  - id: task-cron-tasks
  - boundary: 新建 `src/nexuscli/runtime/cron.py`（`~/.nexuscli/cron.json` 任务表：cron 表达式 + prompt；解析下次触发时间；不做常驻守护，提供 list/next/run 操作，run 由用户或外部调度器触发）；`src/nexuscli/entrypoints/cli.py`（nexuscli cron 子命令组）；新建 `tests/test_cron.py`（表达式解析、下次触发计算（固定 now 注入）、list/run 行为）
  - verify: `uv run python -m pytest tests/test_cron.py tests/test_cli.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W5 轮完成：24 passed（13 新用例：7 参数化非法表达式 + 6 行为）；vixie OR 语义 + 周 7→0 规范化 + 固定 now 注入；cli 仅增 cron_app 组照抄 mcp_app 模式；出入⑦回写：factory.py 缝隙实际形态经出入⑧裁决升级为关键字参数方案（见 task-otel-telemetry 证据）

> W5 冲突检查：otel 与 io-dump 共写 config.py + openai_compatible.py → 已加 depends-on 串行；embedded-rg{file_ops.py, tests/test_tools.py} 与 cron{runtime/cron.py, cli.py, tests/test_cron.py} 与前两者不相交。

## 阶段六：W6 REPL/会话类 P2 与 usage 历史（4 任务，全串行——均共写 repl.py）

- [x] 本地 usage 历史统计（C5，M）
  - id: task-usage-history
  - depends-on: task-workflow-engine
  - boundary: 新建 `src/nexuscli/llm/usage_store.py`（SQLite `~/.nexuscli/usage.db`：每回合 tokens/cost/model 落库，全部参数绑定）；`src/nexuscli/entrypoints/repl.py`（/usage stats [N天]：近 N 天总 tokens、总成本、按模型分布；既有当次回合显示保留）；新建 `tests/test_usage_history.py`（落库、汇总查询、空库零值；monkeypatch Path.home 到 tmp）
  - verify: `uv run python -m pytest tests/test_usage_history.py tests/test_llm_usage.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W6 轮完成：15 passed（5 新用例）；主会话复核零动态 SQL（6 处 execute 全 ? 元组绑定、DDL 静态串）；/usage stats 接线与 best-effort 落库边界（派发回合不落库）已入 docstring
- [x] /fork 会话分叉（P2）
  - id: task-fork-session
  - depends-on: task-usage-history
  - boundary: `src/nexuscli/session/store.py`（fork：复制现有会话 JSONL 为新 session_id，meta 记录 fork 来源）；`src/nexuscli/entrypoints/repl.py`（/fork [title]：分叉当前会话并切换到新会话继续）；`tests/test_session_store.py`（fork 后新会话含全部历史、原会话不变、meta 关联）
  - verify: `uv run python -m pytest tests/test_session_store.py tests/test_repl.py` 全绿
  - evidence: 2026-09-29 W6 轮完成：24 passed（5 新用例）；forked_from meta 三处（store.py:37/:49/:63，主会话复核）；fork 读文件前先落盘的注释在位；goal 不随 fork 复制的取舍已入 docstring
- [x] 会话长程目标 Goal（P2）
  - id: task-session-goal
  - depends-on: task-fork-session
  - boundary: 新建 `src/nexuscli/context/goal.py`（`~/.nexuscli/goals/<session_id>.json` 的 GoalStore：set/show/clear）；`src/nexuscli/entrypoints/repl.py`（/goal set <text>、/goal show、/goal clear；设置后每回合在用户输入前拼接目标提醒短前缀——与 /skill 强制加载共用注入点，实现时抽公共注入函数）；新建 `tests/test_goal.py`（CRUD、注入前缀出现、清除后不再注入）
  - verify: `uv run python -m pytest tests/test_goal.py tests/test_context.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W6 轮完成：18 passed；与 /skill 强制加载共用注入点并抽公共 _push_turn_context（repl.py:1105，双回合分支 :267/:297 调用，主会话复核）；清除后不再注入有测试；test_skill_force_load 回归 3 passed
- [x] /effort 推理力度切换（P2）
  - id: task-effort-switch
  - depends-on: task-session-goal, task-otel-telemetry
  - boundary: `src/nexuscli/entrypoints/repl.py`（/effort minimal|low|medium|high：会话内切换即时生效，无参显示当前值）；`src/nexuscli/llm/openai_compatible.py`（chat 请求按当前 effort 附 reasoning_effort 类字段——按 provider 能力白名单，不支持的不发送）；新建 `tests/test_effort_switch.py`（stub client 断言请求体字段正确；不支持的 provider 不携带）
  - verify: `uv run python -m pytest tests/test_effort_switch.py tests/test_llm_usage.py` 全绿 + `uv run ruff check .` 全绿
  - evidence: 2026-09-29 W6 轮完成：23 passed（13 例含参数化）；provider 白名单 {openai, openai-compatible, compatible} 附键、deepseek/glm/kimi/anthropic 不携带均有 stub 断言（openai_compatible.py:228，主会话复核）；无参显示当前值、/model 重置声明在位

> W6 冲突检查：四任务均写 repl.py → 全串行 depends-on 链（usage-history → fork → goal → effort）；effort 另写 openai_compatible.py，已依赖 W5 的 otel/io-dump 链尾。

## 阶段七：W7 收尾——文档同步、Mimosa 审计、证据归档（3 任务，主会话执行，串行）

- [x] 文档统一同步与锚点核验
  - id: task-final-docs-sync
  - depends-on: task-bgstop-kill-ladder-test, task-subagent-turns-cap, task-hooks-new-events, task-microcompact, task-skill-force-load, task-hooks-sha256-trust, task-pdf-read, task-anthropic-native, task-mcp-oauth, task-trace-id, task-plugin-local-mvp, task-bg-subagent-notify, task-workflow-engine, task-subagent-memory, task-subagent-channel, task-otel-telemetry, task-llm-io-dump, task-embedded-rg, task-cron-tasks, task-usage-history, task-fork-session, task-session-goal, task-effort-switch
  - boundary: 只改 `README.md`、`TUTORIAL.md`（如需）、`docs/` 下文件；内容与实现逐项核对——命令表（/skill 强制加载、/workflow、/expert、/goal、/effort、/fork、/usage stats、cron 子命令、task run_in_background）、工具表（PDF、mailbox）、配置项（agent.max_turns 子代理上限、telemetry.enabled、llm.debug_dump）、hooks 事件表（PermissionRequest/PostToolUseFailure）与 sha256 信任机制；不虚构任何未实现行为
  - verify: 逐锚点 grep 核验 README/TUTORIAL/docs 全部锚点与内链 0 断链（核验命令与结果记入 checklist 证据）+ `uv run ruff check .` 全绿（确认未误改代码）
  - evidence: 2026-09-29 W7 轮完成（两段：同步 + 修复）：81 内链 0 断链（slug 校验脚本）+ 修复轮 168 锚点 0 断链重跑；独立核查员抽查 12+ 条声明对照实现，3 处问题（observability.md 重复句、README:356 run-id 口径、payload 枚举不全）已修复；主会话复验三处修复落盘、ruff 全绿、490 passed；声明→出处对照表在 .zcode/round17 报告
- [x] Mimosa 完整安全审计复跑（B1，主会话执行）
  - id: task-mimosa-audit
  - depends-on: task-final-docs-sync
  - boundary: 不改业务代码；发现的可执行问题回写本包 tasks.md 并修复后复扫；扫描摘要记入 checklist 验收证据
  - verify: 主会话运行 Mimosa security_scan（depth=deep，project=E:/Code/nexusCLI）：扫描完成且无 blocker 级发现；若出现 blocker，回写为可执行任务修复并复扫至无 blocker（凭据类发现遵循「只从环境变量读取、源码/测试零凭据字面量」约束）
  - evidence: 2026-09-29 主会话执行：Mimosa security_scan depth=deep 完成，scanId=scan-2026-09-29T10-47-38.288Z-6de6c0aba849，封印 sha256:49d7859a281621c7c9c8771e7962bfeeb7f7d2aba78df41cf777e52a066a8df3，findingCount=0（零发现、零 blocker）；51 包扫描、1 条离线依赖 advisory（offlineAdvisory matched）；evidenceBoundary=static_only_no_runtime_execution。时序说明：扫描启动于文档修复轮之前，但扫描对象为代码（代码侧全部任务已收官冻结），后续变更仅 markdown 文档，不影响结论
- [x] 全量回归与验收证据补全
  - id: task-checklist-evidence
  - depends-on: task-mimosa-audit
  - boundary: 只更新本包 `checklist.md`（证据与勾选）、`spec.md` / `tasks.md`（状态同步）
  - verify: `uv run python -m pytest`（≥338+新增 passed）、`uv run ruff check .`、`uv run ruff format --check .` 三条尾行记入验收证据；`python E:/Code/.refs/spec-skill/scripts/check_spec_package.py --slug 2026-09-29_add-zcode-backlog --root E:/Code/nexusCLI` 无未通过门禁
  - evidence: 2026-09-29 主会话执行：三条尾行记入 checklist（490 passed in 27.87s / All checks passed! / 141 files already formatted）；check_spec_package 复核过程中抓出 W4 三任务漏翻框（编排方勾选疏漏，含本轮共三次同类失误，均由脚本或评审代理兜底发现）并当场修正，终跑收敛

---

**当前进度**：由脚本计算，无需手动维护
