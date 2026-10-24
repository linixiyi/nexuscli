# NexusCLI 可视化工作台与 README 分层（2026-10-25 autoplan）

> 来源：用户需求——调研 [kitagent](https://github.com/zed123214/kitagent) 框架能否移植到本项目制作可视化工作台；
> 同轮优化 README 可读性。
> 已确认决策：工作台 **MVP 先行**（审批卡片等作后续特性，autorun 后续轮次补齐）；前端 **React + Vite**；
> README **重排+分层**（收敛 ~400 行，细节下沉 docs/）；autorun 全程 **只 commit 不 push**。
> 2026-10-25 范围修订（framework-2，用户授权）：基于 kitagent **源码级调研**（clone 实测：702 测试全绿、
> core mypy 清零），把其三个源码验证的强项引入本项目——工具副作用日志与幂等重放（F9）、回合级恢复与
> 引擎结果合同（F10/F11）、provider 细节与 mypy 纪律（F12/F13），新增第 5-7 轮。

## 目标

给 NexusCLI 增加一个浏览器可用的可视化工作台（借鉴 kitagent 的「一个 Core、多种入口」架构模式，
在现有 Runtime API 上实现，不移植其运行时本体），让用户在 Web 里完成会话查看、回放、任务管理、
用量统计，并逐步闭环审批与发起 turn；同轮把 743 行的 README 重排为分层结构，提高可读性。
验收口径：每个特性都能说清「用户会怎么用到」和「修完/做完怎么证明」，全部特性由 autorun
分轮实现并勾选。

## 移植评估结论（框架级）

kitagent 是一套自洽的完整运行时（React 19 前端 + FastAPI 网关 + 常驻 Core + JSON-RPC/NDJSON +
Anthropic provider + Loop/LangGraph 引擎 + K8s 沙箱），**整体移植不可行也不划算**：它替换而非复用
NexusCLI 的引擎。可移植的是它的**架构模式**——「一个 Core、多种入口」：NexusCLI 已有等价地基
（进程内引擎 + Runtime API 的 threads/turns/events/tasks REST 端点 + 会话 JSONL 转录 + usage
落库 + HITL/审计），缺的只是呈现层：Web 前端、流式推送（现为轮询式 events）、Web 审批呈现。
因此本计划做「NexusCLI Workbench」：在现有 Runtime API 上叠一层工作台，不引入 kitagent 运行时。

## 已确认事实

- Runtime API（`src/nexuscli/runtime/api.py`）为标准库 `http.server` 实现，端点：POST /v1/threads、
  POST /v1/threads/{id}/turns、GET /v1/threads/{id}/events、/v1/tasks 四组；无 SSE/WebSocket。
- Python 依赖零 Web 框架（httpx/prompt-toolkit/rich/typer/mcp/pillow）；工作台服务端不得新增 Python 依赖。
- 会话转录 `~/.nexuscli/sessions/<id>.jsonl`（首行元信息）、用量库 `~/.nexuscli/usage.db`、
  计划 checkpoint、后台任务报告均已落盘，工作台只做读取与呈现。
- 仓库已有生成内容入库先例（`site/` 文档站），`frontend/dist` 构建产物允许提交。
- pytest 环境经 `tests/conftest.py` autouse fixture 重定向 HOME/USERPROFILE，新增测试不得绕开。

## 本轮特性（autorun 按轮迭代实现，勾选 = 已实现并验证）

### 第 1 轮：README 分层重排

- [x] F1 README 重排分层：开头 TL;DR + 特性速览表，正文重排为「快速上手 → 核心概念 → 深入专题」
      三段式；长实现细节下沉 docs/ 对应文档并保留锚点链接；README 收敛到 ~400 行；
      全部目录锚点与外链有效，内容不丢失只重新分层

### 第 2 轮：工作台 MVP

- [x] F2 服务端工作台面：Runtime API 新增 SSE 事件流端点（thread/turn 事件实时推送）与静态资源
      托管（`serve --workbench` 或等价入口，默认绑定 localhost）；零新增 Python 依赖
- [x] F3 React + Vite 前端工程：`frontend/` 目录（Vite + React + TypeScript），构建产物
      `frontend/dist` 提交并由服务端托管；dev 模式经 Vite proxy 指向 Runtime API；构建与运行方式写入文档
- [x] F4 会话视图：会话列表 + 转录回放（读会话 JSONL 与 thread events），消息/工具调用时间线呈现，
      刷新后仍可继续观察（SSE 或轮询续看）
- [x] F5 任务面板 + usage 统计视图：/v1/tasks 列表/状态/取消操作呈现 + `~/.nexuscli/usage.db`
      汇总（回合数、Token、成本）呈现

### 第 3 轮：交互闭环

- [x] F6 Web 审批卡片：Runtime API 任务审批挂起的 Web 呈现与批准/拒绝动作，与 REPL 审批同一语义
      （同一审计口径），审批提示带 Reason 行
- [x] F7 Web 端发起 turn：工作台输入框提交 prompt 建 thread/turn，SSE 实时观察流式输出与工具调用

### 第 4 轮：CI 与文档收尾

- [ ] F8 工作台 CI 与文档：CI 增加 frontend 构建校验；新增 docs/workbench.md（架构、端点、边界）；
      README 增加工作台章节与截图占位

### 第 5 轮：工具副作用日志（恢复底座）

- [ ] F9 工具调用日志与幂等重放：新增独立 SQLite 工具日志——三态
      `started / completed / outcome_unknown` + 规范化输入哈希身份校验 + 序列化结果落库，
      语义对标 kitagent `graph/recovery.py`（claim→execute/reuse/fail-closed，同 id 异参数为
      类型化冲突）；接入有副作用的内置工具（写类 bash、write_file、edit_file）；
      `/plan --resume`、team checkpoint 与后台任务恢复路径经日志去重：已 `completed` 的调用
      直接复用结果不再执行，`outcome_unknown` 一律 fail-closed 不自动重放

### 第 6 轮：回合级恢复与引擎合同

- [ ] F10 ReAct 回合级 checkpoint 与断点续跑：主代理 ReAct 循环每轮后原子落盘（对话消息、
      待处理工具调用、usage 基线），提供从 checkpoint 接续被中断回合的入口；与既有
      `/plan --resume`（计划级）并存并明确三套持久化（会话 JSONL / plan checkpoint /
      回合 checkpoint）的真相分工；fail-closed 依赖 F9 日志（`outcome_unknown` 的调用不自动重放）
- [ ] F11 引擎结果合同统一：react / plan / team 的分发边界收敛为显式 EngineResult
      （终态 | 挂起）类型合同，审批等待、协作式中断、预算耗尽成为一等挂起值而非异常或 None，
      错误带类型化错误码；为 F6 Web 审批与 F10 恢复提供统一语言；对齐 kitagent
      `RunOutcome | RunSuspension` 的 sum-type 形态

### 第 7 轮：provider 细节与静态检查

- [ ] F12 Anthropic provider 细节对齐：`max_tokens` 截断落在工具调用中间时合成
      `is_error` tool_result 保住对话块配对；thinking 块带 signature 原样回传的核实与补齐；
      传输层流断线（RemoteProtocolError / ReadError / ConnectError）按 1/2/4s 退避重试且
      不重试语义错误；prompt caching 打位（system + 最后一个工具）。先盘点现状缺什么再补，
      已具备的补核实测试
- [ ] F13 mypy 渐进接入 CI：配置 mypy，存量模块按排除清单豁免、新增与本轮改动模块必须通过，
      CI 增加 mypy job；排除清单只减不增，最终目标全仓清零（对标 kitagent core 100 文件清零）

## 细节文档

- [01-workbench-server.md](01-workbench-server.md) — F2/F6 服务端面（SSE、静态托管、审批桥）+ F4/F5 的服务端读端点（/v1/sessions、/v1/sessions/{id}/transcript、/v1/usage/summary）
- [02-workbench-frontend.md](02-workbench-frontend.md) — F3/F4/F5/F7 前端工程与视图；F6 的呈现半边（审批卡片组件与轮询）也在此
- [03-readme-restructure.md](03-readme-restructure.md) — F1 README 重排分层
- [04-ci-and-docs.md](04-ci-and-docs.md) — F8 CI 与文档收尾
- [05-execution-durability.md](05-execution-durability.md) — F9-F13 执行可靠性：工具日志、回合 checkpoint、引擎结果合同、provider 细节、mypy

## 边界（本轮明确不做）

- 不移植 kitagent 运行时本体：Loop/LangGraph 引擎、Anthropic provider、K8s 沙箱、常驻 Core 进程
  模型均不引入
- **不引入 LangGraph 依赖**：F10/F11 需要的 checkpoint / 挂起原语在既有 plan DAG 执行器与
  ReAct 循环上自研等价物，不新增重依赖、不做两套状态真相
- **不照搬 kitagent 的多租户恢复外壳**：能力令牌 / 恢复租约是为常驻多客户端服务设计的；
  NexusCLI 取其语义（fail-closed、幂等重放、类型化冲突、只存哈希），认证外壳按单用户 CLI +
  Runtime API 现状裁剪，是否需要等价令牌机制下沉 05-detail 裁决
- F13 为渐进接入：不承诺本轮全仓 mypy 清零，只承诺排除清单只减不增
- 不做 OS 级沙箱、多用户认证与公网部署；工作台默认只绑定 localhost（与 MCP server 的部署边界一致）
- MVP 不引入前端单元测试框架（vitest 等）：以「构建通过 + Python 侧端点 smoke」为验收；
  前端测试基建挂到 F8 之后再议
- 不改变 REPL/TUI 既有交互；工作台是新增入口，不动既有 CLI 行为；REPL 的 Ctrl+C 仍是中止
  而非协作式中断（挂账不变）

## 假设与开放问题

- 假设：`frontend/dist` 入库可接受（有 `site/` 先例）；若用户后续反对，改为构建说明 + 空态提示页。
- 已定稿（原开放问题的去向）：SSE 连接生命周期与断线重连（since 游标）→ 01-workbench-server.md；
  会话 JSONL 读取一致性 → 02-workbench-frontend.md；README 下沉映射表 → 03-readme-restructure.md。
- 开放（下沉 05-execution-durability.md 定稿）：工具日志库的位置、schema 版本化与轮换策略；
  F10 回合 checkpoint 与会话 JSONL / plan checkpoint 的三套持久化真相分工；F11 结果合同的
  覆盖入口（REPL / SDK / Runtime API）与改造半径；F12 现状盘点结论（哪些已具备、缺哪些）。
