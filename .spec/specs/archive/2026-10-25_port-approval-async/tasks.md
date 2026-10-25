# 对齐第 3 轮：审批异步化（A9） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：挂起与恢复闭环

- [x] 审批挂起分支：任务路径审批 → RunSuspension + turn_checkpoint 挂起态
  - boundary: 只改 runtime/approvals.py（挂起态登记）、agent/query_engine.py 或 runtime/api.py
    （任务路径折叠层拦截审批事件 → 落挂起态 + 返回挂起语义）、agent/turn_checkpoint.py（挂起载荷：
    pending 调用 + approval_id + revision）、agent/results.py（如需新事件类型）；同步回调路径零变化；
    新增 tests/test_approval_async.py 挂起/恢复/超时三场景
  - verify: `uv run python -m pytest -q tests/test_approval_async.py`
- [x] 决策恢复端点与超时
  - depends-on: task_0001
  - boundary: 只改 runtime/api.py（decision 端点对挂起任务触发恢复：approve → 重派原调用经 journal、
    deny → 合成 deny；300s expired → 合成 deny 沿用 F6 口径）+ 测试补恢复与超时场景
  - verify: `uv run python -m pytest -q tests/test_approval_async.py tests/test_runtime_approvals.py tests/test_runtime.py`

## 阶段二：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0002
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master A9 + .spec/autorun/ 摘要
  - depends-on: task_0003
  - boundary: 只改 master（A9 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] A9 " .spec/plans/2026-10-25_kitagent-alignment/master.md`（期望输出 1）
