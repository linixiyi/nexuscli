# nexusCLI ZCode 差距存量台账清偿（28 项） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-09-29 19:05
- 说明：28 项存量（4 note + 2 跟进 + 8 P1 + 14 P2）全部清偿——24 个任务交付 + 4 项带证据处置；测试 338 → 490 passed（+152），ruff/format 全绿，uv sync --extra dev --frozen 通过，Mimosa 深度扫描 0 发现（封印 sha256:49d7859a281621c7c9c8771e7962bfeeb7f7d2aba78df41cf777e52a066a8df3）

## 假设回顾
### 已验证假设
- 台账规模（S/M/L）沿用 round2-brief 第五节排波，七波实际执行与估算一致，无一波超限或返工重排
- 凭据依赖类任务（MCP OAuth、原生 Anthropic、OpenTelemetry）按 spec 2.2 假设以 mock 验证协议层，全部通过；真实端到端联调留待用户
- pypdf 与 opentelemetry SDK 入 dev 组后 `uv sync --extra dev --frozen` 可复现（收尾实测 Checked 51 packages，退出码 0）
- mailbox 仅子代理注册（tests/test_subagent_mailbox.py 白名单回归线钉住）；P2 全量交付（W2-W6 全部落地）

### 仍未完全验证的假设
- OTel span 形态以 in-memory exporter 验证，真实 collector 端到端未接（文档已如实披露支持边界）

## 交付范围
### 已交付
- W1：kill 升级阶梯测试、子代理 SUBAGENT_MAX_TURNS=30、hooks 补 PermissionRequest/PostToolUseFailure、微压缩 microcompact、/skill load（+波内文档收口）
- W2：hooks sha256 信任、PDF 读取（pypdf 可选组）、原生 Anthropic 客户端（mock）、MCP OAuth PKCE（mock）、traceId 全链路
- W3：插件系统本地目录 MVP、后台子代理与回合末通知
- W4：动态工作流 JSON 步骤图 + /workflow 与 /expert、子代理持久记忆、子代理 mailbox
- W5：OpenTelemetry（no-op 降级、工厂关键字参数 + 7 调用点接线）、LLM IO 调试落盘（脱敏）、嵌入式 rg 直通、Cron 离线任务
- W6：usage 统计（SQLite 参数绑定）、/fork、/goal、/effort（provider 白名单）
- W7：全量文档同步（README/TUTORIAL + docs/ 七个新文件，锚点 0 断链）、Mimosa 深度审计、证据归档
- 全部 24 个任务的 verify 命令实跑证据（tasks.md 各 evidence 行）

### 未交付
- REPL 真机交互 smoke（external_blocked：需真实 TTY，owner 用户）
- OTel/Anthropic/MCP OAuth 的真实端点端到端（按 2.2 假设为 mock 口径，联调由用户执行）
- 常驻 cron 守护、并行工作流执行器、TS 编译器等价物（spec 3.3 明确范围外）

## 简化决策
- /expert 并入动态工作流引擎（共用 JSON 步骤图与入口层，spec 2.4）
- W6 四任务串行链（均共写 repl.py，串行成本低于拆波管理开销，spec 2.4）
- telemetry 与 debug_dump 均为单一布尔开关默认关闭，未做可插拔抽象（spec 4）
- 子代理记忆目录不做写入旁路：默认 path_guard 下带降级文案，持久记忆主通道维持 save_memory（文档已披露）

## 变更边界
- 新增模块：telemetry/、plugins/、workflow/、agent/mailbox.py、agent/bg_tasks.py、llm/usage_store.py、llm/anthropic.py、mcp/auth.py、hooks/trust.py、context/goal.py、runtime/cron.py、observability.py
- 修改集中在 repl.py、cli.py、executor.py、builtins.py、config.py、subagent.py、openai_compatible.py、factory.py、session/store.py、file_ops.py、slash_commands.py、bootstrap.py
- 文档：README.md、TUTORIAL.md 与 docs/ 九文件；依赖：+pypdf、+opentelemetry（可选组，uv.lock 同步）
- boundary 外改动共八处出入（①-⑧），全部经编排方逐项裁决并回写 tasks.md evidence，无未声明改动

## 验证证据
- uv run python -m pytest → 490 passed in 27.87s（退出码 0；基线 338 passed in 25.96s）
- uv run ruff check . → All checks passed!；uv run ruff format --check . → 141 files already formatted（基线 110）
- uv sync --extra dev --frozen → 退出码 0（Checked 51 packages）；uv run nexuscli --help → 退出码 0（--version → nexuscli 0.1.0）
- Mimosa security_scan（depth=deep）：findingCount=0，scanId=scan-2026-09-29T10-47-38.288Z-6de6c0aba849
- 六项平台能力回归线（test_permissions/test_hooks/test_subagent/test_slash_commands/test_context/test_plan_mode + test_permission_mode）全程绿色

## 门禁证据
- check_spec_package.py（--slug 2026-09-29_add-zcode-backlog --root E:/Code/nexusCLI）：27/27 任务完成、检查清单全勾、功能框全勾、已收敛
- check_all_spec_packages.py：推送前全量校验通过（本归档为 v1 问题处置格式）
- 提交：375a865（spec/add-zcode-backlog 分支，Spec footer）

## 问题处置

```json
{
  "version": 1,
  "issues": [
    {
      "id": "otel-factory-signature",
      "summary": "W5 简报缝隙授权假设工厂收到完整 config，实际 create_llm_client 形参为 LlmConfig，按简报字面接线必 AttributeError（实测 2 用例红、启动路径断）",
      "taskId": "task-otel-telemetry",
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "编排方裁决方案 B：factory.py 加 telemetry_enabled 关键字参数（默认 False 向后兼容）+ 7 处生产调用点接线（主会话 grep 逐一核实）；test_factory_wiring_produces_chat_span 防回退；--version/--help 实测通过；出入⑧已回写 tasks.md"
    },
    {
      "id": "rg-bare-skip-globs",
      "summary": "W5 简报把 skip globs 作为裸位置参数拼进 rg 命令，实测被 rg 当搜索路径（退出码 2），快速路永久死路",
      "taskId": "task-embedded-rg",
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "file_ops.py 改为逐个 --glob !dir/** 传递 + Popen 加 utf-8/replace 防 cp936 解码崩溃；两处修正均有探针实测记录；tasks.md evidence 回写"
    },
    {
      "id": "hooks-new-events-matcher",
      "summary": "W1 hooks 新事件的非 * matcher 被 registry._TOOL_EVENTS 硬编码集静默忽略（简报\"自动继承\"判断错误）",
      "taskId": "task-hooks-new-events",
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "实现代理按\"报告不改道\"上报；主会话验收补 frozenset 两键 + test_new_tool_events_honor_matcher 用例；全量 351 passed 当时转绿"
    },
    {
      "id": "mcp-oauth-dns-validation",
      "summary": "MCP OAuth token URL 校验只查 IP 字面量与 localhost 主机名，不做 getaddrinfo DNS 解析（SSRF 面留有理论缺口）",
      "taskId": "task-mcp-oauth",
      "actionable": true,
      "disposition": "accepted_risk",
      "owner": "维护者",
      "rationale": "保留 TLD 测试需要而省略 getaddrinfo DNS 解析；取舍写入 _validate_token_url docstring，docs/mcp.md 已披露；Mimosa 深度扫描 0 发现",
      "reviewTrigger": "接入真实 OAuth MCP 服务时补 DNS 解析并加打桩用例复测",
      "evidence": "file_ops 无关；mcp/auth.py:129-152 仅查 IP 字面量与 localhost；扫描封印 sha256:49d7859a…"
    },
    {
      "id": "subagent-memory-pathguard",
      "summary": "子代理持久记忆目录 ~/.nexuscli/agent-memory/ 在默认 path_guard_enabled=true 下不可由子代理文件工具写入",
      "taskId": "task-subagent-memory",
      "actionable": true,
      "disposition": "accepted_risk",
      "owner": "维护者",
      "rationale": "path_guard 工作区写域是有意安全边界，不为此放宽守卫；模型侧持久记忆主通道维持 save_memory",
      "reviewTrigger": "若引入受信记忆路径白名单机制，重评估子代理目录直写",
      "evidence": "subagent.py:183-195 降级文案；README 与 docs/subagents.md 如实披露"
    },
    {
      "id": "a4-nonnumeric-maxturns",
      "summary": "非数值 agent.max_turns（如字符串）加载阶段回退默认 200 而非报错",
      "taskId": "task-w1-docs-sync",
      "actionable": true,
      "disposition": "accepted_risk",
      "owner": "维护者",
      "rationale": "回退为 config.py docstring 写明的有意设计且有测试钉住，报错反而破坏配置容错",
      "reviewTrigger": "若 agent.max_turns 配置笔误高发，考虑加载阶段输出告警日志",
      "evidence": "config.py:435-449 实现；tests/test_agent_turns.py:185 断言 abc→200；README 配置节补说明行（A4 闭环）"
    },
    {
      "id": "orchestration-checkbox-misses",
      "summary": "编排方（主会话）勾选任务包时三次漏翻复选框（插件任务、otel 任务、W4 三任务）",
      "taskId": "task-checklist-evidence",
      "actionable": true,
      "disposition": "resolved_current",
      "evidence": "三次均由独立评审代理或 check_spec_package.py 兜底发现并当场修正；终态 27/27 全勾且脚本收敛；教训入项目记忆（每框单独编辑）"
    },
    {
      "id": "repl-manual-smoke",
      "summary": "交互式 REPL 真机 smoke（Shift+Tab 三态、/help 渲染、/compact 实时输出）未执行",
      "taskId": "task-checklist-evidence",
      "actionable": true,
      "disposition": "external_blocked",
      "dependency": "真人交互式 TTY 会话，自动化测试无法替代按键流",
      "owner": "用户",
      "retryTrigger": "下次真人使用 REPL 时按 checklist 手工验证栏执行 Shift+Tab 三态、/help 渲染与 /compact 实时输出",
      "evidence": "--help/--version 命令行冒烟 exit 0；490 项自动化测试覆盖非交互面；处置记录于 tasks.md task-checklist-evidence"
    }
  ]
}
```
