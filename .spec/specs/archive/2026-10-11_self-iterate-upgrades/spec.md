# nexusCLI 自我迭代波：四项升级与主流 Agent 对标 - 项目范围

## 1. 问题定义

- **项目目标**：兑现 2026-10-11 差距评估的全部非人工项——四项升级（usage 落库覆盖子代理、BM25 检索排序、/workflow 并行执行器、外围真实冒烟），并调研主流 AI Agent（含用户点名的 pi，本机实测）产出差距清单，自我迭代实现其中高价值小项。
- **目标用户**：nexusCLI 开发者与 BYOK 用户。
- **核心价值**：用量口径完整、检索可用性提升、工作流引擎并行化；产品从「对标参考项目」走向「对标主流 Agent 生态」。

## 2. 已确认事实（2026-10-11 本机实测）

- 基线：main=660746d，pytest 590 passed。
- 调研覆盖 8 个产品：pi（本机 @earendil-works/pi-coding-agent v1.1.0 实测+读包内源码，source=refs）、Claude Code / OpenCode / ZCode（.refs 本地文档）、Codex CLI / Gemini CLI / Aider / Cursor CLI（模型知识，source=knowledge）。
- 差距清单 14 条；本轮实现 3 条：provider 瞬时错误（429/5xx）自动压缩重试、Agent Skills 规范兼容、provider key 经命令/密钥管理器读取。
- 外围真实冒烟 3/3：team 模式（total 108465 tokens，答案含两文档标题）、mcp serve http tools/list 返回 25 工具、Runtime API 200/401 鉴权正确；key 零落盘。
- 复审 8 条（4 medium：workflow usage 误标 repl、session_id 守卫前提不成立、并行步骤共享邮箱收件箱、零 token 双计）经集成结构性处置：调度器不再落库，每步 usage 由 run_subagent done 事件统一记 source=subagent（零 token 跳过）、每步独立 mailbox_name 后缀。

## 3. 验证级别

`package`：ruff check / ruff format --check / pytest 全量（与 CI 同口径）+ Mimosa 深度扫描 + 外围真实 LLM 冒烟。
