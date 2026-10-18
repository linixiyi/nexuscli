# 测试矩阵收口:17 项零验证表面 + 45 项仅机器表面的真实使用验证 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 `docs/testing-matrix.md` 里 17 个 ⬜（零机器验证、零真实运行）与 45 个 🟡（有断言、从未真实跑过）的表面收口：⬜ 项补机器验证 + 真实运行，🟡 项补真实运行，模拟真人使用，让矩阵从「断言通过」变成「真人能用的证据」
- **目标用户**：真实使用 nexuscli 的终端用户（交互 REPL、脚本 `-p`、Runtime 接入方）
- **核心价值**：每个面向用户的表面都有「机器验证 + 至少一次真实运行留痕」，回归不再是静默的

## 2. 假设与待确认

### 2.1 已确认事实
- 真实网关可用：2026-10-18 主线程实测 `-p "只回复两个字:在线"` 返回正常、exit=0
- `~/.nexuscli/config.json` 已配置 llm；上轮（fix-audit-residual）948 passed / 3 skipped、ruff 全绿
- 上轮反馈 = 矩阵文档「仍然没测的（下一轮优先）」：⬜ 17 项、🟡 45 项（含 /team /workflow /expert 插件 自定义命令 hooks @image: PDF 等高频项）
- 斜杠命令 dispatch 集中在 `entrypoints/repl.py`（elif 链）；工具注册集中在 `tools/builtins.py`——两者为跨 lane 共享文件

### 2.2 关键假设
- 🟡 项实现大多已对（有断言），真实运行主要是补证据 + 暴露观感问题；⬜ 项才可能挖出真缺陷
- 真实运行可用 pty 驱动 REPL + 真实进程 `-p` / 子命令 + 真实网关完成，不需要人工在场

### 2.3 待确认问题
- 无（范围由用户直接给定：17 ⬜ + 45 🟡，多轮工作流，每轮以上轮反馈为输入，最后推 GitHub）

### 2.4 可选解释与取舍
- 当前选择：6 条并行 lane 按文件所有权划分 -> 共享文件（repl.py、builtins.py、cli.py、slash_commands.py、agent/agent.py）对所有 lane 只读，跨 lane 修复进下轮反馈，避免并行编辑冲突
- 当前选择：每轮独立 CreateWorkflow，主线程回收反馈后拉下一轮 -> 契合用户「每轮根据上轮的反馈继续拉起新的工作流」的要求，主线程保住验收与门禁

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] Lane A 会话与快照：/save /resume /fork /snapshot /restore、8.4 快照回滚、8.5 vfs 只读视图（7 项，含 4 个 ⬜）
- [x] Lane B 记忆与索引：/memory /index /search、7.1 静态记忆、7.2 动态记忆、7.3 Top-K 召回、7.4 自动压缩、7.6 索引检索、save_memory/search_memory（10 项，含 3 个 ⬜）
- [x] Lane C 模式与子代理：/team（REPL 内 + worker-mode=plan）、/task、task 工具、2.8 循环检测、2.9 超限重试、2.10 plan 续跑、/workflow、12.6 JSON DAG、12.7 邮箱、12.8 后台子代理（10 项，含 2 个 ⬜）
- [x] Lane D 技能/插件/扩展：12.1-12.5、6.8 hooks、6.9 hooks 信任门禁、/init、/expert、/goal、load_skill/save_skill（12 项）
- [x] Lane E 工具面/MCP/SDK：directory_tree、get_file_info、search_code、todo_write/read、edit_file、revert_turn、/hitl、/model 选择器、/mcp、10.2 工具过滤、10.3 OAuth 落盘、11.9 SDK（12 项，含 8 个 ⬜）
- [x] Lane F 可观测性与媒体：13.1 trace_id、13.2 OTel、13.3 llm.debug_dump、13.4 usage 落库、14.1-14.2 图片、14.3 降级、14.4 PDF、14.5 LSP 诊断（9 项）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 9 个 🟠（仅真人）项补断言：doctor、--version、/context、/tools、/policy、/usage、空 prompt Ctrl+C、Ctrl+D、bracketed paste——本轮聚焦用户指定的 62 项
- 不改事件流协议、审批四档语义；不重排目录结构
- 共享文件（repl.py、slash_commands.py、cli.py、tools/builtins.py、agent/agent.py）的结构性改动

## 4. 最小实现路径
- 最简单可行方案：每条 lane = 一个子代理，先补 ⬜ 项的机器验证（新测试文件），再对全部 62 项做真实运行（pty 驱动 REPL / 真实进程调用 / 真实网关），证据写进包内 `evidence/lane-X.md`；发现的缺陷在自有边界内就修，跨边界记入下轮反馈
- 暂不引入：新的测试框架、录制回放工具、mock 网关
- 不做的抽象/配置化：不把真实运行脚本产品化，证据以一次性脚本 + 留痕为准

## 5. 技术决策
- 技术栈：pytest + uv 环境真实运行（pty / subprocess / 真实网关）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：各 lane 所有权文件（见 tasks.md boundary）+ 各 lane 新建测试文件 + docs/testing-matrix.md（仅主线程收口时）
- 本轮不应触碰：repl.py、slash_commands.py、cli.py、tools/builtins.py、agent/agent.py 的修改（所有 lane 只读；需要的修复记入下轮反馈，由主线程在轮间收口）
- Git integration branch：`spec/2026-10-18_verify-zero-and-yellow`

### 5.1 验证策略
- 范围级别：package
- 变更对象：62 个表面的测试与真实运行证据；lane 所有权模块内的小修
- 快速检查：`.venv/bin/python -m pytest -q <lane 新增测试文件>`（lane 内）
- 集成检查：`.venv/bin/python -m pytest -q`（每轮工作流末尾全量跑一次）
- 全项目检查：仅当 lane 修复触碰共享基础设施、跨模块契约或发布载荷时升级；本包两轮工作流末尾与主线程收口均已全量跑 pytest + ruff（升级条件已满足并执行）
- 升级触发：若 lane 修复触碰共享文件或跨模块契约，升级为 round 2 反馈项并记录

## 6. 成功标准与验证方式
- [x] 17 个 ⬜ 表面每个都有机器验证（新测试）与真实运行留痕 -> verify: 各 lane 测试文件 + evidence 文件
- [x] 45 个 🟡 表面每个都有一次真实运行留痕（44 ✅；14.2 上游环境阻塞，CLI 契约 wire 级已证，如实标注）-> verify: evidence/lane-X.md + evidence/round2-*.md
- [x] 真实运行发现的缺陷全部处置（13 个本轮修复带回归测试；其余按 checklist 处置记录分级）-> verify: manual: checklist.md 处置记录
- [x] 全量 pytest + ruff 无回归 -> verify: `.venv/bin/python -m pytest -q` + `.venv/bin/python -m ruff check src tests`

## 7. 风险与约束
- 风险点：并行 lane 真实网关调用相互叠加触发限流 -> 缓解：子代理按需最小调用；runtime 自适应重试
- 风险点：真实运行暴露共享文件缺陷但 lane 无权修 -> 缓解：记入 `issuesForNextRound`，主线程轮间收口后由下一轮复验
- 风险点：62 项一轮跑不完/跑不深 -> 缓解：多轮工作流，每轮以上轮反馈为输入，直到收敛
