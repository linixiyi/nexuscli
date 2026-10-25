# 对齐第 2 轮：审批与会话语义（A5-A8） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：并行实现（子代理 /workflow 三 lane；文件面不相交）

- [x] A5+A6 lane：审批状态机 + 会话权限模式与撤销（approvals.py/websession.py/api.py 串行）
  - boundary: 只改 src/nexuscli/runtime/approvals.py（五态 + decide 幂等 + input 截断禁 allow）、
    runtime/websession.py（SessionPermissions 会话态 + revision + revoke + recheck 挂起钩子）、
    runtime/api.py（PATCH /v1/workbench/permissions 端点 + decision 端点接状态机）、
    tests/test_approval_semantics.py（新建）
  - verify: `uv run python -m pytest -q tests/test_approval_semantics.py tests/test_runtime_websession.py tests/test_runtime_approvals.py`
- [x] A7 lane：事件 correlation/event_seq 续号
  - boundary: 只改 src/nexuscli/agent/agent.py 与 runtime/api.py 的事件发射/写入点（同 run 单调 seq；
    SSE 帧 data 内增 seq 字段，帧格式向后兼容）+ tests/test_event_seq.py（新建）；不改事件类型集
  - verify: `uv run python -m pytest -q tests/test_event_seq.py tests/test_runtime_sse.py`
- [x] A8 lane：transcript 哈希提交链 + 孤儿修剪
  - boundary: 只改 src/nexuscli/session/store.py（写路径追加行级哈希链列/字段 + 孤儿 tool_use 修剪）
    与 runtime/reads.py（读时校验开关）+ tests/test_transcript_integrity.py（新建）；不改 JSONL 既有字段
  - verify: `uv run python -m pytest -q tests/test_transcript_integrity.py tests/test_runtime_reads.py tests/test_session_store.py`

## 阶段二：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0001, task_0002, task_0003
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master A5-A8 + .spec/autorun/ 摘要
  - depends-on: task_0004
  - boundary: 只改 master（A5-A8 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] A[5-8] " .spec/plans/2026-10-25_kitagent-alignment/master.md`（期望输出 4）
