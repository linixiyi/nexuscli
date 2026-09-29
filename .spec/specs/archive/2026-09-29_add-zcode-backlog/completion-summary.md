# 完成总结：2026-09-29_add-zcode-backlog

## 目标与结果

把 2026-09-28 ZCode 差距分析遗留的 28 项存量台账（4 note + 2 跟进 + 8 P1 + 14 P2）全量清偿：**24 个任务交付、4 项带证据处置**，无一悬空。测试 338 → **490 passed**（+152），ruff/format 全绿，`uv sync --extra dev --frozen` 通过，Mimosa 深度扫描 **0 发现**（封印 sha256:49d7859a281621c7c9c8771e7962bfeeb7f7d2aba78df41cf777e52a066a8df3）。

## 按波交付

- **W1**：kill 升级阶梯测试、子代理回合上限（SUBAGENT_MAX_TURNS=30）、hooks 补 PermissionRequest/PostToolUseFailure、微压缩 microcompact、/skill 强制加载（+波内文档收口）
- **W2**：hooks sha256 信任、PDF 读取（pypdf 可选组）、原生 Anthropic 客户端（mock）、MCP OAuth PKCE（mock）、traceId 全链路（审计+X-Request-ID）
- **W3**：插件系统本地目录 MVP（三类注册表并入、坏 manifest 安全跳过）、后台子代理与回合末通知
- **W4**：动态工作流 JSON 步骤图引擎 + /workflow 与 /expert 入口（串行拓扑序最小切片）、子代理持久记忆（带 path_guard 降级披露）、子代理 mailbox
- **W5**：OpenTelemetry 遥测（no-op 降级、7 处工厂接线）、LLM IO 调试落盘（脱敏）、嵌入式 rg 直通（--glob 修正 + 编码防护）、Cron 离线任务
- **W6**：本地 usage 统计（SQLite 参数绑定）、/fork 会话分叉、/goal 长程目标（与 /skill 共用注入点）、/effort（provider 白名单）
- **W7**：全量文档同步（README/TUTORIAL + docs/{hooks,mcp,subagents,workflows,cron,observability,plugins}.md，声明→出处对照，锚点 0 断链）、Mimosa 审计、证据归档

## 处置项（4）

- A2 smoke 声明 → resolved_current（基线提交 853fe83 提交说明承载）
- A4 非数值 max_turns 回退 → accepted_risk（tests/test_agent_turns.py:185 钉住）
- B2 REPL 真机 smoke → external_blocked（owner 用户，需真实 TTY）
- /expert → 并入 task-workflow-engine（共用引擎与入口层）

## 编排形态与回写记录

17 轮动态工作流自迭代（GLM-5.3 总规划与验收、GLM-5.3-Flash 实现与波次简报）。八处 boundary 出入（①-⑧）全部经编排方裁决并回写 tasks.md evidence；三次编排方勾选漏翻框均由评审代理或 check_spec_package 脚本兜底发现并当场修正。

## 已知边界与遗留

- OTel / Anthropic / MCP OAuth 为 mock 口径（spec 2.2 假设），真实端到端联调由用户自行执行；OTel 未接真实 collector
- MCP OAuth `_validate_token_url` 不做 DNS 解析（保留 TLD 测试需要，docstring 记录；Mimosa 扫描 0 发现）
- 子代理记忆目录在默认 path_guard_enabled=true 下不可由子代理文件工具写入（文档已如实披露，持久记忆主通道为 save_memory）
- usage 落库覆盖面：/init、/plan、/workflow、/expert 派发回合不落库（docstring 与文档均已披露）
