# NexusCLI 真实使用场景审计与缺口修复 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：以「开发者日常真实使用」为验收口径，跑一遍 NexusCLI 的全部主要入口（REPL、单次 `-p`、plan/team、工具面、联网、MCP、Runtime API、cron、eval、SDK），把口头宣称与真实行为不一致、日常会卡住或误导使用者的地方找出来并修掉。
- **目标用户**：仓库作者本人（长时间高频终端使用）与按 README 上手的外部使用者（他们信任 README 的每一句"支持"）。
- **核心价值**：把「功能存在」升级为「在日常使用里真的可用」；每一项结论都带可复现命令与真实输出，不留"应该可以"。

## 2. 假设与待确认

### 2.1 已确认事实
- 基线（2026-10-18 本机实测，HEAD=26bea2e）：`.venv/bin/python -m pytest` → 823 passed, 3 skipped；`ruff check` 全通过；`ruff format --check` 194 files 已格式化。
- 真实 LLM 可用：本地网关 `http://localhost:20128/v1`（provider=openai-compatible，model=`cbcn/glm-5.3-flash`），`nexuscli -p` 端到端跑通（16.6s / 26.6s 两次实测，工具路径返回事实正确）。
- 单次 `-p` 模式**没有**审批回调：非只读工具（`bash` 的写类命令、`write_file`、`edit_file`、`task`、`save_memory`）在该模式下被一律判 deny，模型收到的是 `Tool "bash" was denyed by approval policy.`——其中 `denyed` 是错误拼写，且提示语没有告诉用户"如何在非交互模式放行"。
- 工作区外路径守卫生效：`write_file` 写 `/tmp/...` 被拒，报 `path escapes workspace`。
- docs/README 的事实底数是 `.venv/bin/nexuscli` 真实可跑的：31 个 slash 命令、25 个内置工具（+2 个 vfs 视图）、`eval list` 三个内置场景、`cron`/`mcp`/`serve`/`worker` 子命令都在。

### 2.2 关键假设
- 评审以「一个真实开发者每天会做的事」为准：读代码、改代码、跑命令、切模型/力度、断点续跑、看用量、出错恢复。
- 真实 LLM 网关是本机沙箱资源，跑失败可重试；不引入任何新的外部依赖。
- 修法以最小可逆改动为主，不重排模块结构；观感层不回归已确认的单色前景约束。

### 2.3 待确认问题
- [x] 非交互 `-p` 模式的放行语义选哪种 → 结论：保持 deny 默认（安全优先），但提示语必须写明放行途径（`permissions.allow` / `policy.hitl_mode`），并在 README 如实写清"脚本化场景需要显式声明 allow 规则"。

### 2.3.1 审计结论（本轮实际交付）
- 三路只读审计共 48 条发现，本轮修复 11 条（4 条 P0 + 6 条 P1 + 1 条 P2），其余 11 条按严重度挂账，逐条证据与复现入口见 [audit-report.md](audit-report.md)。
- 修复后基线：`.venv/bin/python -m pytest` → 889 passed, 3 skipped；`ruff check` / `ruff format --check` 全绿。

### 2.4 可选解释与取舍
- 当前选择：先修「真实使用会立刻踩到」的缺陷（提示语、文档口径、测试污染真实 home、非交互可用性），不追求把每个子系统都重写一轮 -> 理由：日常使用里最先卡住的就是这些，且都能用一条命令验证。

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] F1：非交互 `-p` 模式下被审批策略拒绝时的提示语可读、可行动（拼写正确 + 指明放行途径）
- [x] F2：测试不再向真实用户 home 写审计日志（`~/.nexuscli/audit.jsonl` 被 pytest 污染）
- [x] F3：README/docs 与真实行为对齐（非交互审批语义、`/effort` 七档、工具表）
- [x] F4：审计发现的其余 P0/P1 缺陷逐条修复并有测试

### 3.2 扩展功能
- 无（本轮不做：目录重构、provider 新增、观感再设计）

### 3.3 不在范围内
- 不改事件流、审批四档语义、会话转录格式、usage 台账口径
- 不重排 `src/nexuscli` 目录结构；不做观感层重设计
- 不引入 OS 级沙箱

## 4. 最小实现路径
- 最简单可行方案：按审计清单逐条修，每条配一条能真实跑出来的验证命令（测试或 CLI 冒烟），最后跑全量 pytest + ruff + 一次真实 `-p` 冒烟。
- 暂不引入：新的配置项、新的抽象层、跨平台打包改动。
- 不做的抽象/配置化：不把审批策略重构成可插拔后端，只修语义与文案。

## 5. 技术决策
- 技术栈：Python 3.11+ / rich / prompt-toolkit / httpx / mcp（现状不变）
- 证据协议：evidence: runs（机器任务 verify 命令由 `scripts/spec_verify.py run` 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：`src/nexuscli/tools/executor.py`、`src/nexuscli/entrypoints/`、`tests/`、`README.md`、`docs/*.md`、`.spec/`
- 本轮不应触碰：`site/` 生成产物、`uv.lock`、CI workflow（除非审计发现 P0）
- Git integration branch：`spec/2026-10-18_fix-real-usage-gaps`

### 5.1 验证策略
- 范围级别：package
- 变更对象：本任务包 boundary 涉及的模块、入口和数据契约
- 快速检查：`.venv/bin/python -m pytest -q <变更对应测试文件>`
- 集成检查：`.venv/bin/python -m pytest`（全量 823 passed 基线不得回归）
- 全项目检查：仅在升级触发时执行 `.venv/bin/python -m ruff check .` + `.venv/bin/python -m ruff format --check .`；本轮为 package 级，逐任务只跑对应测试文件，全量 pytest 作为集成检查兜底
- 升级触发：改动共享基础设施、跨模块契约或发布载荷时升级到项目级并记录理由

## 6. 成功标准与验证方式
- [x] 审计清单里的 P0/P1 缺陷全部有处置结论（修复或如实挂账）-> verify: task_0016
- [x] 全量测试与 ruff 不回归 -> verify: task_0016
- [x] 真实 `-p` 冒烟通过（含工具调用路径）-> verify: task_0016

## 7. 风险与约束
- 风险点：真实 LLM 网关不稳定导致冒烟偶发失败 -> 缓解：冒烟失败先重试一次，仍失败则记录原始输出并挂账，不用"可能"措辞糊过去。
- 风险点：改审批提示语会动到断言渲染字符串的测试 -> 缓解：同步更新 `tests/test_permissions.py` 与相关断言。
- 风险点：子代理可能误改仓库文件 -> 缓解：审计 lane 明确只读，且改动只在本分支由主线程落地。
