# 工作台交互闭环（F6-F7） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：服务端

- [x] ApprovalBridge 桥 + 两端点 + 单元语义测试
  - boundary: 新增 src/nexuscli/runtime/approvals.py（PendingApproval 登记表、callback_for 绑定、APPROVAL_TIMEOUT_SECONDS=300、超时 deny resolved_by=timeout）+ api.py 两路由（GET /v1/approvals、POST /v1/approvals/{id}/decision，仅 approve/deny，Origin 语义同 MCP A3）；新增 tests/test_runtime_approvals.py 的桥单元与端点语义用例（含二次决策 404、非法动词 400、超时 deny）
  - verify: `uv run python -m pytest -q tests/test_runtime_approvals.py`
- [x] 执行器集成与 Reason 契约测试
  - depends-on: task_0001
  - boundary: 只在 tests/test_runtime_approvals.py 增执行器集成用例（write_file 挂起→approve 落盘+审计 approver=hitl；deny 拒绝；deny 规则下登记表恒空；description 键非空）；不改 executor.py
  - verify: `uv run python -m pytest -q tests/test_runtime_approvals.py tests/test_tool_approval_matrix.py`
- [x] _run_turn / _run_task 接入审批回调
  - depends-on: task_0001
  - boundary: 只在 src/nexuscli/runtime/api.py 的 _run_turn/_run_task 给 QueryEngine 增加 approval_callback=approval_bridge.callback_for(...)（thread/task 绑定）；不改工具层与引擎
  - verify: `uv run python -m pytest -q tests/test_runtime.py tests/test_runtime_approvals.py`

## 阶段二：前端

- [x] ApprovalCard + 2s 轮询 + Composer 状态机
  - depends-on: task_0003
  - boundary: 只写 frontend/src/（api/approvals.ts 消费 GET /v1/approvals 与 decision、App 外壳常驻审批轮询与卡片区、views/Composer.tsx 提交状态机 + 线程记忆写入、TranscriptView 支持 F7 提交态）；frontend/dist 重建；不改 Python 侧
  - verify: `cd frontend && npm run build && cd .. && test -z "$(git status --porcelain -- frontend/dist)" && echo "dist in sync"`

## 阶段三：收口

- [ ] 全量回归 + 端到端 smoke
  - depends-on: task_0004
  - boundary: 不改产品文件；CI 三步 + serve --workbench 端到端（无 LLM key 环境按 02 口径做错误路径断言并如实标注）
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [ ] 计划同步：勾选 master F6/F7 + .spec/autorun/ 摘要
  - depends-on: task_0005
  - boundary: 只改 master（F6/F7 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F[67] " .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 2）
