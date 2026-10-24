# 工具副作用日志与幂等重放（F9） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：新增 SQLite 工具调用日志（三态 started/completed/outcome_unknown + 输入哈希身份校验），
  接入三类有副作用的内置工具，让中断后的恢复路径能复用已完成副作用、对结果未知的调用 fail-closed
- **目标用户**：用 /plan --resume、team worker、后台任务恢复的长任务用户
- **核心价值**：防「中断前已完成同一写入、恢复后盲目重放」；completed 结果直接复用不再执行

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约：05-execution-durability.md F9 节（claim 四分支/两级重放语义/run_id 延续硬约束/接入范围三类工具/DDL）
- 库位置 `~/.nexuscli/tool_journal/journal.db`（conftest HOME 重定向覆盖）；schema 版本化 version=1
- run_id 延续是两级机制前提：ExecutionPlan 增 run_id 字段、TaskRecord 增 run_id 列（既有 alter-table 惯例）、回合内 run_id 每次 Agent.run() 的 uuid4 hex

### 2.2 关键假设
- tool_use_id 级精确重放要到 F10（/turn-resume 持久化 pending 原始 id）才兑现；本轮先落日志与 hash 级尽力去重
- 日志写失败在副作用发生后必须原样上抛、不得触发重执行（05 第 2 条）

### 2.3 待确认问题
- 无（05 已定稿全部裁决）

### 2.4 可选解释与取舍
- 当前选择：首版不做自动轮换 -> 单用户体量有界，手动 SQL 清理写入文档，prune 命令挂账

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [ ] F9 工具调用日志与幂等重放：journal 模块（三态 CHECK/输入哈希/claim 四分支/序列化结果落库）+ executor 接线（write_file/edit_file 非 dry_run/bash 写类）+ run_id 延续（plan/task/run 注入）+ hash 级尽力去重

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- /turn-resume（F10）；EngineResult 合同（F11）；MCP/task/save_memory 等其余副作用工具接入（声明式清单挂账）
- 自动轮换/prune 命令；对任意 shell/MCP/外部服务的 exactly-once 承诺（诚实边界）

## 4. 最小实现路径
- 最简单可行方案：单文件 src/nexuscli/tools/journal.py + executor 单一分发点接入 + plan/task 两处 run_id 顺延
- 暂不引入：journal 异步写、WAL 之外的调优、多租户能力令牌
- 不做的抽象/配置化：journal 开关不做 config 项（模块常量启用；失败上抛语义不做静默降级开关）

## 5. 技术决策
- 技术栈：Python stdlib sqlite3 + hashlib
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/tools/journal.py（新建）、tools/executor.py（分发点接入）、tools/builtins.py（仅当接线需要时的最小触点）、plan/models.py（run_id 字段）、runtime/tasks.py（run_id 列迁移）、agent 编排层 run_id 生成注入、tests/
- 本轮不应触碰：llm/、REPL 渲染、runtime/api.py 对外契约、.github/、docs/
- Git integration branch：`spec/2026-10-25_add-tool-journal`

### 5.1 验证策略
- 范围级别：package
- 变更对象：tools/journal.py（新）、executor 分发点、plan/models 与 runtime/tasks 的 run_id 面
- 快速检查：`uv run python -m pytest -q tests/test_tool_journal.py tests/test_executor_journal.py`
- 集成检查：`uv run python -m pytest -q tests/test_plan_checkpoint.py tests/test_lane_c_team_task.py tests/test_bg_subagent.py`（run_id 顺延路径）
- 全项目检查：适用外——与 CI 一致的三步回归由收口任务执行
- 升级触发：若 executor 接入改变既有工具结果形状（非新增）或破坏任何既有测试语义，升级 integration 并记录

## 6. 成功标准与验证方式
- [ ] journal 模块：三态 CHECK、schema 版本化（无元数据/版本不符拒绝）、claim 四分支（execute/reuse/outcome_unknown/conflict）、complete 落库序列化结果、异常转 outcome_unknown -> verify: task_0001
- [ ] executor 接线：write_file 两次同参调用第二次 reuse 不落盘重写；started 残留 → outcome_unknown 错误结果；同 id 异参 → 类型化冲突；bash 只读直通不进日志 -> verify: task_0002
- [ ] run_id 延续：plan checkpoint 载荷含 run_id；TaskRecord 含 run_id 列且旧库迁移不炸 -> verify: task_0003
- [ ] hash 级去重：同 run_id 内 (tool_name, input_hash) 命中 completed → 复用 -> verify: task_0004
- [ ] 全量回归（CI 三步）不破 -> verify: task_0005
- [ ] master F9 勾选、.spec/autorun/ 摘要写入 -> verify: task_0006

## 7. 风险与约束
- 风险点：journal 写失败把正常执行变成失败 -> 语义即如此（副作用后失败必须上抛，防静默重复）；测试锁该语义
- 风险点：executor 是全工具热路径 -> 只对三类工具走 journal 分支，只读直通零开销
- 风险点：run_id 透传改动 plan/task 契约 -> to_dict 增字段向后兼容（旧 checkpoint 无 run_id 视为新 run）
