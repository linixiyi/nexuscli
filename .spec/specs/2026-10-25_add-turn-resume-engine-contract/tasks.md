# 回合级恢复与引擎结果合同（F10/F11） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：F11 合同

- [x] src/nexuscli/agent/results.py：EngineResult 合同与错误码
  - boundary: 只新建 results.py（RunOutcome/RunSuspension/EngineErrorDetail/EngineRunResult、
    SuspensionReason 三态、码表 llm_error/plan_no_checkpoint/tool_invocation_conflict/outcome_unknown/agent_failed、
    classify_error、PlanCheckpointMissing）+ tests/test_engine_results.py 的合同用例；plan/models.py 的
    裸 ValueError 换 PlanCheckpointMissing
  - verify: `uv run python -m pytest -q tests/test_engine_results.py`
- [x] 发射点 error_code 与 plan/team 终态聚合
  - depends-on: task_0001
  - boundary: 只改 agent.py（error 事件增 error_code=llm_error 于流错误发射点）、plan_execute.py（classify_error
    用于 error 事件与终态聚合、checkpoint missing 用新异常）、orchestrator.py（终态聚合点 typed outcome）、
    query_engine.py（`*_result_async` 三变体折叠事件为 EngineResult，既有 raise 语义原样保留）；不改事件形状与工具层
  - verify: `uv run python -m pytest -q tests/test_engine_results.py tests/test_query.py tests/test_plan_checkpoint.py tests/test_lane_c_team_task.py`

## 阶段二：F10 回合恢复

- [ ] src/nexuscli/agent/turn_checkpoint.py：存储与列表
  - depends-on: task_0001
  - boundary: 只新建 turn_checkpoint.py（~/.nexuscli/turn_checkpoints/<id>.json、temp+rename 原子写、
    cwd 过滤列表、损坏降级、schema version=1、载荷：checkpoint_id/run_id/cwd/model/provider/turn/
    messages/pending_tool_calls/usage_so_far/cost_so_far/created_at）+ tests/test_turn_checkpoint.py 存储用例
  - verify: `uv run python -m pytest -q tests/test_turn_checkpoint.py`
- [ ] _run_react 双快照点 + /turn-resume 接线
  - depends-on: task_0003
  - boundary: 只改 agent.py（Agent 参数 turn_checkpoints opt-in，默认 False；两快照点：assistant tool_use
    入 messages 后执行前 / 轮末继续前；载荷含 run_id/usage 基线）与 entrypoints/repl.py（/turn-resume
    列出与接续：重建历史、沿用 checkpoint run_id、usage 初值、pending 按原 id 经执行器+journal 分流
    execute/reuse/outcome_unknown；不新增事件类型）
  - verify: `uv run python -m pytest -q tests/test_turn_checkpoint.py tests/test_agent_turns.py`

## 阶段三：收口

- [ ] 全量回归（CI 三步）
  - depends-on: task_0004
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [ ] 计划同步：勾选 master F10/F11 + .spec/autorun/ 摘要
  - depends-on: task_0005
  - boundary: 只改 master（F10/F11 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F1[01] " .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 2）
