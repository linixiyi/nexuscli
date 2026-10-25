# 对齐第 4 轮：实时推送/离线 Demo/e2e（A10-A12） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：并行实现（子代理 /workflow 三 lane；文件面不相交）

- [x] A10 lane：审批事件入 SSE + token 合帧 + resync
  - boundary: 只改 src/nexuscli/runtime/api.py（_send_event_stream 事件源并入 approvals 登记表
    快照挂起/解决事件；token 合帧缓冲 50ms；队列满下发 resync 语义帧）与
    tests/test_runtime_sse.py（新增用例）；不改帧格式既有字段
  - verify: `uv run python -m pytest -q tests/test_runtime_sse.py`
- [x] A11 lane：离线确定性 Demo provider + e2e 启动器
  - boundary: 新增 src/nexuscli/llm/offline_demo.py（脚本化 step 决策 provider：逐 8 字符流式、
    真实工具调用、走真实审批门；显式注入缝，默认路径零影响）+ scripts/web_validation_server.py
    （stdio 管道交接一次性口令、禁止入日志）+ tests/test_offline_demo.py；不改既有 provider
  - verify: `uv run python -m pytest -q tests/test_offline_demo.py`
- [x] A12 lane：前端 seq-patch + pending 幂等 + Playwright 基线
  - boundary: 只改 frontend/src/state.ts（新：patch 应用器纯函数）、frontend/src/useWorkbench.ts
    （新：bootstrap/pending 持久化/重连编排，键名 nexuscli:pending）、frontend/e2e/workbench.spec.ts
    （新，5 条基线用例）、frontend/package.json（Playwright devDep + test script）；构建产物同步
  - verify: `cd frontend && npm run build && npx playwright test --reporter=line`（期望基线用例通过；浏览器二进制缺失时如实记录）

## 阶段二：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0001, task_0002, task_0003
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master A10-A12 + .spec/autorun/ 摘要
  - depends-on: task_0004
  - boundary: 只改 master（A10-A12 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] A1[012] " .spec/plans/2026-10-25_kitagent-alignment/master.md`（期望输出 3）
