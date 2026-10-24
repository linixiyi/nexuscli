# 工作台 MVP（F2-F5） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：服务端

- [x] 纯重构：提取 `_build_http_server`（port 注入）与 Origin loopback 判定共用函数
  - boundary: 只动 src/nexuscli/runtime/api.py 与 src/nexuscli/mcp/server.py 的函数提取（移到共享位置或各自引用同一实现），行为零变化；不改任何端点语义
  - verify: `uv run python -m pytest -q tests/test_runtime.py tests/test_mcp_http_security.py`（期望全绿）
- [x] SSE 事件流端点 GET /v1/threads/{id}/events/stream
  - depends-on: task_0001
  - boundary: 只在 src/nexuscli/runtime/api.py 加流式端点与 `_authorized` 的 api_key 查询参数扩展（仅 stream 路径）、BoundedSemaphore(16) 槽位、心跳/断开/停机处理；新增 tests/test_runtime_sse.py；不改既有快照端点与其余端点鉴权
  - verify: `uv run python -m pytest -q tests/test_runtime_sse.py tests/test_runtime.py`（期望全绿，含鉴权不外溢负向断言）
- [x] 静态托管 + serve --workbench 开关 + 空态页
  - depends-on: task_0001
  - boundary: 只在 src/nexuscli/runtime/api.py 加静态分发（路径归一+resolve 防穿越+Content-Type 覆盖表+SPA 回退）与 src/nexuscli/entrypoints/cli.py 加 --workbench 开关；新增 tests/test_runtime_static.py
  - verify: `uv run python -m pytest -q tests/test_runtime_static.py`（期望全绿）
- [x] F4/F5 服务端读端点：/v1/sessions、/v1/sessions/{id}/transcript、/v1/usage/summary
  - depends-on: task_0001
  - boundary: 新增 src/nexuscli/runtime/reads.py（会话 JSONL 首行 meta 扫描、transcript 分页、UsageStore.stats 序列化）+ api.py 三条路由；id 只接受 [0-9a-zA-Z-]+；新增 tests/test_runtime_reads.py
  - verify: `uv run python -m pytest -q tests/test_runtime_reads.py`（期望全绿）

## 阶段二：前端

- [x] frontend/ 工程脚手架与 dist 入库
  - depends-on: task_0002, task_0003
  - boundary: 新建 frontend/（package.json、package-lock.json、vite.config.ts dev proxy /v1、tsconfig strict、index.html、.gitignore、src/ 最小骨架）+ 根 .gitignore 加 !frontend/dist/；构建产物 frontend/dist 提交；tests/test_runtime_static.py 增「已提交 dist」用例（dist 缺失时 pytest.skip）
  - verify: `cd frontend && npm ci && npm run build && cd .. && test -z "$(git status --porcelain -- frontend/dist)" && echo "dist in sync"`
- [x] 前端视图：外壳 + 会话/回放/任务/usage
  - depends-on: task_0004, task_0005
  - boundary: 只写 frontend/src/（App 外壳 key 门+tab、api/ 客户端与 TS 类型对齐 01 契约、sse/useEventStream、views/ 四视图）；不改 Python 侧
  - verify: `cd frontend && npm run build`（tsc -b 类型检查随构建通过）

## 阶段三：收口

- [x] 全量回归 + 真实进程 smoke
  - depends-on: task_0006
  - boundary: 不改产品文件；跑 CI 三步与 serve --workbench 真实 smoke（SSE 回放/心跳、reads、静态首页），结果记入验收证据
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master F2-F5 + .spec/autorun/ 摘要
  - depends-on: task_0007
  - boundary: 只改 .spec/plans/2026-10-25_workbench-and-readme/master.md（F2/F3/F4/F5 置 [x]）与新增 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F[2-5] " .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 4）
