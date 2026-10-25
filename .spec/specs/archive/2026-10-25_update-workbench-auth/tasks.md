# 工作台鉴权重构（一次性口令 + Cookie 会话） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：服务端

- [x] runtime/websession.py + bootstrap 端点 + 会话门
  - boundary: 新建 runtime/websession.py（bootstrap_token/cookie/csrf 三秘密、authorize、bootstrapped 一次性标志）；
    api.py 增 POST /v1/workbench/bootstrap（Set-Cookie HttpOnly SameSite=strict path=/ + JSON csrf）与
    _handle 门改造（cookie 或 api_key 二选一；/v1/workbench/bootstrap 与静态资源免 cookie 门）；
    /v1/* 全面 Origin 白名单（本服务来源与无 Origin 放行）；启动打印一键链接（cli.py）
  - verify: `uv run python -m pytest -q tests/test_runtime_websession.py`
- [x] CSRF 强制与既有通道适配
  - depends-on: task_0001
  - boundary: 只改 api.py（POST/取消类变更请求校验 x-csrf-token，compare_digest；api_key 头请求豁免）与
    既有测试 fixture 的鉴权适配（改用 cookie 或显式 api_key）；新增 CSRF/Origin 用例进 tests/test_runtime_websession.py
  - verify: `uv run python -m pytest -q tests/test_runtime_websession.py tests/test_runtime.py tests/test_runtime_approvals.py tests/test_runtime_sse.py tests/test_runtime_reads.py`

## 阶段二：前端

- [x] 删 key 门，#bootstrap 自动换发 + csrf 内存态
  - depends-on: task_0002
  - boundary: 只改 frontend/src/（App.tsx 换 key 门为 bootstrap 流程、api/client.ts 删 getApiKey 改 csrf 头与
    cookie 凭证、sse/useEventStream.ts 删 api_key 查询参数走 cookie）；frontend/dist 重建
  - verify: `cd frontend && npm run build && cd .. && test -z "$(git status --porcelain -- frontend/dist)" && echo "dist in sync"`

## 阶段三：收口

- [x] 全量回归 + 一键链接 smoke
  - depends-on: task_0003
  - boundary: 不改产品文件；CI 三步 + 真实启动 smoke（打印 #bootstrap 链接、bootstrap 换发、cookie 访问）
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master F14/F15 + .spec/autorun/ 摘要
  - depends-on: task_0004
  - boundary: 只改 master（F14/F15 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F1[45] " .spec/plans/2026-10-25_workbench-auth/master.md`（期望输出 2）
