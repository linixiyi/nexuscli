# 工作台 MVP（F2-F5） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：在现有 Runtime API（标准库 http.server，零新增 Python 依赖）上实现可视化工作台 MVP：SSE 事件流 + 静态托管（F2）、React+Vite 前端工程（F3）、会话列表与转录回放（F4）、任务面板与 usage 统计（F5）
- **目标用户**：想在浏览器里观察会话、任务与用量的 NexusCLI 用户（localhost 单人）
- **核心价值**：多入口落地——REPL 之外获得 Web 观察面，刷新后仍可续看，零新增 Python 依赖

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约：`.spec/plans/2026-10-25_workbench-and-readme/01-workbench-server.md`（F2/F6/F4/F5 服务端面）与 `02-workbench-frontend.md`（F3/F4/F5 前端），本包任务由两者任务派生提示派生
- Runtime API 现状：ThreadingHTTPServer 绑定 127.0.0.1（api.py:75），events 端点为一次性回放（api.py:357-375），鉴权只认两个头（api.py:290-297）；无 CORS、无静态托管
- `UsageStore.stats(days)` 已存在（usage_store.py:162-246）；会话 JSONL 由 SessionWriter 原子整文件重写（store.py:509-524,606-610），服务端整文件读即可
- Node v24.13.1 / npm 11.19.1 在 PATH；frontend/dist 入库获 master 授权（site/ 先例）

### 2.2 关键假设
- frontend/dist 构建产物提交后，`serve --workbench` 开箱即用；Node 仅自行构建时需要
- MVP 无前端单测（vitest 不进本轮）：验收 = 构建通过 + Python 侧端点测试 + 真实进程 smoke

### 2.3 待确认问题
- 无（01/02 已定稿全部设计裁决，含 SSE since 游标、鉴权扩展边界、审批排除本轮）

### 2.4 可选解释与取舍
- 当前选择：SSE 断线重连用 since 游标而非 Last-Event-ID -> EventSource 无法带鉴权头，重连必为手工，游标显式（01 定稿）
- 当前选择：usage 端点直接序列化 UsageStore.stats -> 复用既有聚合，响应形状与 02 的 UsageStats 类型逐字段一致

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] F2 服务端工作台面：SSE 事件流端点 + 静态资源托管 + `serve --workbench` 开关 + F4/F5 服务端读端点（零新增 Python 依赖）
- [x] F3 React + Vite 前端工程：frontend/ 目录、dist 入库、dev proxy
- [x] F4 会话视图：会话列表（/v1/sessions）+ 转录回放（SSE since=0 续看）
- [x] F5 任务面板 + usage 统计视图

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- F6/F7（Web 审批卡片、Web 发起 turn——第 3 轮）；F8（CI 与文档收尾——第 4 轮）；F9-F13
- WebSocket、多用户认证、公网部署；dist 打进 wheel（挂账）
- 前端单元测试框架（vitest 挂 F8 之后）

## 4. 最小实现路径
- 最简单可行方案：服务端全部用 stdlib 在 api.py 上叠端点；前端 Vite react-ts 最小依赖手写脚手架（不用交互式 create）
- 暂不引入：FastAPI/uvicorn/websockets、前端路由库/状态库/UI 组件库/图表库
- 不做的抽象/配置化：APPROVAL_TIMEOUT/MAX_SSE_CLIENTS 保持模块常量（01 挂账④）；审批挂起不进本轮

## 5. 技术决策
- 技术栈：Python stdlib http.server + Vite/React/TypeScript（frontend/ 内）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/runtime/（api.py + 新 approvals 之外的读端点模块）、src/nexuscli/entrypoints/cli.py（--workbench）、src/nexuscli/mcp/server.py（Origin 函数提取共用）、frontend/（新建）、tests/（新增 runtime 测试 + static 增用例）、.gitignore（!frontend/dist/ 与 frontend/.gitignore）、frontend/dist（构建产物）
- 本轮不应触碰：src/nexuscli/agent/、llm/、tools/、.github/、docs/ 正文（docs/workbench.md 归 F8）、.spec/plans/
- Git integration branch：`spec/2026-10-25_add-workbench-mvp`

### 5.1 验证策略
- 范围级别：package
- 变更对象：Runtime API 端点面、serve 入口、mcp/server.py 的 Origin 函数、frontend/ 新工程、相关测试
- 快速检查：`uv run python -m pytest -q tests/test_runtime.py tests/test_runtime_sse.py tests/test_runtime_static.py tests/test_runtime_reads.py`
- 集成检查：`cd frontend && npm ci && npm run build` + dist 同步门禁 `test -z "$(git status --porcelain -- frontend/dist)"`
- 全项目检查：适用外——与 CI 一致的三步回归由收口任务执行；升级条件见「升级触发」行
- 升级触发：本包改 mcp/server.py（Origin 函数提取，跨模块共享面）与 entrypoints/cli.py——若 MCP/server 或 CLI 既有测试暴露回归，就地修复并记录；不改行为语义

## 6. 成功标准与验证方式
- [ ] `_build_http_server` 提取与 Origin 共用函数为纯重构，既有 MCP/runtime 测试不回归 -> verify: task_0001
- [ ] SSE 端点回放+实时+心跳+断开+鉴权扩展（仅本路径）+16 连接上限全有测试 -> verify: task_0002
- [ ] 静态托管（映射/穿越防护/SPA 回退/空态页/Content-Type 表）+ `--workbench` 开关有测试 -> verify: task_0003
- [ ] /v1/sessions、/v1/sessions/{id}/transcript、/v1/usage/summary 三读端点有测试（id 校验/cwd 过滤/stats 形状） -> verify: task_0004
- [ ] frontend/ 工程落地：npm ci + npm run build 通过，dist 入库，静态托管托管真实 dist -> verify: task_0005
- [ ] 前端视图（外壳/会话/回放/任务/usage）随构建通过交付，视图消费的端点契约与 01 一致 -> verify: task_0006
- [ ] 全量回归（CI 三步）+ 真实进程 smoke（serve --workbench + SSE + reads） -> verify: task_0007
- [ ] master F2-F5 勾选、.spec/autorun/ 摘要写入 -> verify: task_0008

## 7. 风险与约束
- 风险点：SSE 在 ThreadingHTTPServer 每连接一线程模型下的线程泄漏 -> 缓解：BoundedSemaphore(16) + 写失败/超时即退出并释放槽位（01 数据流定稿）+ 测试覆盖
- 风险点：api_key 查询参数扩展外溢放宽整个 API 鉴权面 -> 缓解：负向断言锁住其余端点仍 401
- 风险点：npm 网络依赖 -> 缓解：package-lock.json 入库 + npm ci 严格复现；构建失败属环境问题如实报 blocked
- 风险点：mcp/server.py 提取改动影响 MCP server 行为 -> 缓解：提取为纯函数移动 + 既有 MCP 测试回归
