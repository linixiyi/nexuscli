# 首帧体验优化：快照/流式/错误路径/非TTY - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把"一句话输入到看见第一个字"的等待从分钟级压回秒级，并消除非 TTY 管道崩溃——用真人方式在命令行实测 nexusCLI（新网关 127.0.0.1:7864/v1）后定位出的四个首帧体验杀手
- **目标用户**：终端里把 nexuscli 当日常编码 agent 用的人（也含脚本化调用 `-p` 的场景）
- **核心价值**：等待时间可预期、输出即时可见、出错秒级反馈、管道调用不崩——对齐 pi/claude-code/opencode 的首帧体验基线

## 2. 假设与待确认

### 2.1 已确认事实（2026-10-16 实测，E:\Code\nexusCLI，约 600 文件/15MB 工作区，E: 盘单文件 I/O 病理慢 ~100ms/file）
- 事实 A：单发"只回复两个字"全程 2m53s；httpx trace 显示唯一请求 56s 才发出、4.8s 完成，之后又空转 ~56s 才退出（CPU≈0）
- 事实 B：阻塞点在 `SnapshotService.create`（`snapshot/service.py:38` → `shutil.copytree`）：每轮对话强制 pre-turn + post-turn **两次全工作区拷贝**（agent.py:168-170, 203），裸 copytree 实测 62.7s；无任何配置开关
- 事实 C：`-p` 单发模式（`cli.py:_run_prompt`）用 `ask_complete_async` 聚合完才打印，text_delta 全部丢弃——实测 212s 屏幕空白后一次刷出全诗
- 事实 D：401 坏 key 路径 5m36s 才报 `Fatal error: HTTP 401`；trace401 证实只有 1 个请求（0.3s 失败）——全部时间耗在请求前的快照拷贝，无重试问题
- 事实 E：管道喂 slash 命令（非 TTY stdin）REPL 直接爆 rich traceback（`RuntimeError: Event loop stopped before Future completed`），退出码非 0
- 事实 F：CLI 冷启动仅 1.7s；网关裸请求（同 4.9k token、27 tools、stream）TTFB 5.2s/总 5.9s——瓶颈全在客户端本地行为

### 2.2 关键假设
- 假设 A：自动快照改为"默认关闭 + 显式开启"或"仅增量拷贝"后，`/snapshot` `/restore` 手动路径保留，不破坏现有恢复语义
- 假设 B：`-p` 模式实时 echo delta 不会破坏 `--json` 消费者（json 模式保持缓冲一次性输出）

### 2.3 待确认问题
- [x] ~~自动快照默认值：默认关（性能优先）还是默认开+增量（安全优先）~~ → 已解决（本轮按默认关闭、配置项开启、增量拷贝落地，取舍记录于 2.4。）

### 2.4 可选解释与取舍
- 当前选择：快照优化走"配置开关 + 增量拷贝（manifest 按 mtime+size 复用已有快照文件）"而非硬链接/写时复制 -> 理由：跨盘（E:→C:）硬链接不可用，CoW Windows 不通用；增量拷贝纯标准库、可测
- 备选被拒：仅按需在文件修改轮快照 -> 被拒理由：需要侵入 react 循环判定"修改"，复杂度高于配置+增量

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 功能 A：自动快照可关——`features.auto_snapshot`（默认 false）；关闭时 pre/post-turn 不再拷贝，`/snapshot` 手动 create/restore/clean 行为不变
- [x] 功能 B：增量快照——`create()` 带 manifest（相对路径+mtime+size），与上一份快照比对，未变文件直接 `shutil.copy2` 复用源（跳过已有目标且指纹一致的文件），语义仍是完整可 restore 的树
- [x] 功能 C：`-p` 流式输出——单发模式消费事件流，text_delta 实时写到 stdout（`--plain` 与默认皆流式；`--json` 保持一次性输出）；模式行/工具调用行为不变
- [x] 功能 D：非 TTY 优雅降级——stdin 非 tty 时不再崩 traceback：给出一行清晰提示并以退出码 1 退出（引导用 `-p`），不做完整 headless REPL（留后续轮）

### 3.2 扩展功能
- 无（第二轮候选：`-p` 等待期 spinner/计时、快照尊重 .gitignore、REPL 流式 UI 打磨、文档 TUTORIAL 更新）

### 3.3 不在范围内
- 不改 react 循环/团队模式逻辑；不改 LLM 客户端协议层；不做写时复制/硬链接；不改 `--json` 输出契约；不做 headless 多行管道 REPL

## 4. 最小实现路径
- 最简单可行方案：A+B 改 `snapshot/service.py`+`config.py`（FeatureConfig 加 auto_snapshot，agent.py 按开关跳过 create）；C 在 `_run_prompt` 改用 `engine.ask(...)` 事件流 echo delta；D 在 `start_repl` 入口检测 `sys.stdin.isatty()`（Windows 下也适用于管道）
- 暂不引入：git-based checkpoint、文件监听、异步快照线程
- 不做的抽象/配置化：不为快照做策略插件；增量 manifest 不做跨会话持久格式（每次与最新一份快照目录即时比对）

## 5. 技术决策
- 技术栈：Python 3.12 标准库（shutil/pathlib/json），rich 已有；pytest 已有
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：`src/nexuscli/snapshot/service.py`、`src/nexuscli/config.py`（FeatureConfig）、`src/nexuscli/agent/agent.py`（快照开关判断）、`src/nexuscli/entrypoints/cli.py`（_run_prompt 流式 + 非 TTY 检测）、`src/nexuscli/entrypoints/repl.py`（非 TTY 降级入口）、对应 tests/*
- 本轮不应触碰：llm/* 协议层、react/team 循环、--json 契约、MCP、权限/HITL
- Git integration branch：`spec/2026-10-16_ux-firstimpression-optimization`

### 5.1 验证策略
- 范围级别：package
- 变更对象：snapshot 服务、FeatureConfig、单发入口、REPL 入口及其测试
- 快速检查：`uv run pytest tests/test_snapshot_service.py tests/test_effort_switch.py tests/test_cli_single_prompt.py -q`（按实际新增/受影响测试文件为准）+ `uv run pytest -q`（全量回归，repo 现有基线 665+）
- 集成检查：`uv run pytest -q` 覆盖（config/snapshot/cli 跨模块契约由现有测试网守护）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或 package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] 标准 A：默认配置下 `-p "hi"` 端到端 < 15s（快照不再拦路） -> verify: task_0001/0002 + manual 实测记录
- [x] 标准 B：`-p` 模式首个可见字符出现时间 ≈ 网关 TTFB（流式生效，非一次性刷出） -> verify: task_0003
- [x] 标准 C：`echo x | nexuscli`（非 TTY）输出一行引导信息、退出码 1、无 traceback -> verify: task_0004
- [x] 标准 D：全量 pytest 回归通过，无既有测试破坏 -> verify: task_0005

## 7. 风险与约束
- 风险点：增量快照指纹比对在有大量新文件时退化为全量 -> 缓解：manifest 即时比对实现简单，正确性优先；性能数据写入 checklist 证据
- 风险点：`-p` 流式与 `--json`/管道消费者的输出契约冲突 -> 缓解：`--json` 分支保持缓冲；`--plain`/默认在非 tty 下也流式（脚本用户可用 --json 拿结构化）
- 风险点：E: 盘病理慢导致 verify 计时波动 -> 缓解：计时断言用宽松阈值或仅记录数值不断言上限
