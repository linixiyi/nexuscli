# 回合级恢复与引擎结果合同（F10/F11） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：F10——ReAct 回合级 checkpoint（每轮双快照点）+ `/turn-resume` 断点续跑（沿用原
  run_id，pending 调用按原 id 经 journal 重派）；F11——EngineResult（终态|挂起）sum type 合同统一
  react/plan/team 分发边界，类型化错误码
- **目标用户**：长任务被中止后想从最近完整轮接续的用户；需要机器可读终态的 SDK 调用方
- **核心价值**：进程死了回合可接续（不只是计划级）；挂起成为一等返回值；错误码可分类

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约：05-execution-durability.md F10/F11 节（双快照点/恢复入口/三套持久化真相分工表/
  sum type 形态/三态挂起/approval_pending 挂账不接线/改造半径写死）
- F9 已落：journal 三态、run_id 延续（ExecutionPlan/TaskRecord）；本轮回合 checkpoint 载荷
  增 run_id；pending 重放按原 id 经 executor+journal
- 转录只在整回合完成后追加（session_cmds.py:21-29），checkpoint 恒领先或等于 JSONL——分叉
  裁决留档不实现

### 2.2 关键假设
- checkpoint 明文落盘沿用 plan_runs 口径；REPL Ctrl+C 仍是中止（价值=从最近完整轮接续）
- F7 的 SSE/REPL 渲染事件流协议一字不动

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：/turn-resume 先行 REPL 命令，顶层 --resume-turn flag 挂账 -> 先验证语义再扩 CLI 面
- 当前选择：F11 只加 `*_result_async` 变体不动既有 raise 语义 -> SDK 兼容零破坏

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [ ] F10 回合级 checkpoint 与断点续跑：turn_checkpoints 存储（原子写）+ _run_react 双快照点 +
  /turn-resume（列出/接续：重建历史、沿用 run_id、usage 初值、pending 按 journal 重派）
- [ ] F11 引擎结果合同：results.py（EngineResult sum type/三态挂起/错误码表/classify_error）+
  agent/plan_execute/query_engine 接线 + SDK `*_result_async` 变体

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- approval_pending 生产者（挂账：F6 同步阻塞回调无挂起出口）；顶层 --resume-turn flag；
  Runtime API 面改动；信号级协作式中断；plan worker 的 journal 透传（F9 已挂账）
- checkpoint 加密（沿用 plan_runs 明文口径，文档如实说明）

## 4. 最小实现路径
- 最简单可行方案：turn_checkpoint.py 单文件存储 + agent.py 两处快照写 + repl.py 一条命令；
  results.py 单文件合同 + 发射点加 error_code + query_engine 折叠变体
- 暂不引入：checkpoint 压缩/加密、TTL 清理（沿用 plan_runs 无清理口径，文档说明）、
  signal 级中断
- 不做的抽象/配置化：checkpoint 开关不做 config 项（Agent 参数 opt-in，REPL 开启）

## 5. 技术决策
- 技术栈：Python stdlib（json/tempfile+rename 原子写）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/agent/results.py（新）、turn_checkpoint.py（新）、agent.py、
  plan_execute.py、orchestrator.py、query_engine.py、plan/models.py（PlanCheckpointMissing 取代裸 ValueError）、
  entrypoints/repl.py（/turn-resume + 命令表）、tests/
- 本轮不应触碰：tools/（journal 已就位）、llm/、runtime/api.py、entrypoints/cli.py、docs/、.github/
- Git integration branch：`spec/2026-10-25_add-turn-resume-engine-contract`

### 5.1 验证策略
- 范围级别：package
- 变更对象：agent 编排层返回合同、ReAct 循环快照点、REPL /turn-resume、SDK 变体
- 快速检查：`uv run python -m pytest -q tests/test_turn_checkpoint.py tests/test_engine_results.py`
- 集成检查：`uv run python -m pytest -q tests/test_plan_checkpoint.py tests/test_agent_turns.py tests/test_query.py`
- 全项目检查：适用外——与 CI 一致的三步回归由收口任务执行
- 升级触发：若事件流协议（text_delta/tool_result/done/error 形状）被迫改动即升级并记录

## 6. 成功标准与验证方式
- [ ] turn_checkpoint 存储：原子写、cwd 过滤列表、损坏降级、schema 版本 -> verify: task_0001
- [ ] _run_react 双快照点：in-flight 快照含 pending 原始 id、轮末快照 pending 为空且覆盖 -> verify: task_0002
- [ ] /turn-resume：列出候选；接续重建历史并沿用 run_id；pending 按 journal 分流（execute/reuse/outcome_unknown）-> verify: task_0003
- [ ] EngineResult 合同：sum type/三态挂起/classify_error 码表；error 事件带 error_code -> verify: task_0004
- [ ] SDK `*_result_async` 变体返回合同且既有 raise 语义不破 -> verify: task_0005
- [ ] 全量回归（CI 三步）不破 -> verify: task_0006
- [ ] master F10/F11 勾选、.spec/autorun/ 摘要写入 -> verify: task_0007

## 7. 风险与约束
- 风险点：_run_react 双快照点拖慢热路径 -> 每轮两次本地 JSON 覆盖写（同 plan_runs 量级），实测回归把关
- 风险点：pending 重放语义与 journal 交互复杂 -> 按 05 分流表逐场景测试；outcome_unknown 不自动重放
- 风险点：REPL 命令接线面宽 -> /turn-resume 只走 Agent 重建 + 事件流，不加新事件类型
