# 工作台（Workbench）

> 本文是 [README](../README.md)「🖥️ 可视化工作台」章节的完整版。

工作台是 NexusCLI 的浏览器入口：「一个 Core、多种入口」中的 Web 形态。服务端 = 现有
Runtime API（标准库 `http.server`，零新增 Python 依赖）+ SSE 事件流 + 静态资源托管；
前端 = `frontend/`（Vite + React + TypeScript），构建产物 `frontend/dist` 由服务端托管。

## 快速开始

仓库检出自带已构建的 `frontend/dist`，直接可用：

```bash
NEXUSCLI_RUNTIME_API_KEY=dev-key uv run nexuscli serve --workbench --port 8080
# 打开终端打印的 http://127.0.0.1:8080/#bootstrap=… 一键链接完成一次性认证
```

启动时终端会打印 `#bootstrap=` 一键链接：前端读出 URL 片段里的一次性口令（随即抹除）
换发 HttpOnly cookie 会话；直接打开首页而无口令时，页面会提示回终端复制该链接。

自行构建前端（需要 Node.js 22 LTS 与 npm）：

```bash
cd frontend && npm ci && npm run build
```

`frontend/dist` 缺失时首页显示空态页与构建指引，`/v1/*` API 不受影响。开发模式下
`cd frontend && npm run dev` 经 Vite proxy 同源转发 `/v1` 到 Runtime API（默认 8080，
可用 `NEXUSCLI_WORKBENCH_API_PORT` 覆盖）。

## 功能面

- **会话视图**：会话列表（服务端枚举 + 本浏览器线程记忆 + 手动 ID）与转录回放——线程走
  SSE `since=0` 全量回放并可继续观察进行中的 turn，REPL 会话走 `/v1/sessions/{id}/transcript`
  静态渲染
- **发起 turn**：输入框建线程提交（react/plan/team 三模式），POST 作为后台提交发出，
  SSE 实时观察流式输出与工具调用
- **任务面板**：后台任务列表 2s 轮询、详情展开、取消（与 `/v1/tasks` 数据一致）
- **usage 统计**：近 N 天回合数/Token/成本汇总卡与按模型、按来源两张拆分表
- **审批卡片**：Runtime 任务挂起的审批在页顶出现（Reason 行 + 批准/拒绝），2s 轮询

## 端点与 API

工作台消费的端点（鉴权：浏览器走「一次性 `#bootstrap` 口令 → HttpOnly SameSite=strict
cookie → 变更请求（POST）另带 `x-csrf-token` 头 → Origin 白名单（带 `Origin` 头且非
本机回环来源一律 403）」三件套；外部通道保留 `x-api-key` 头，SSE 额外支持 `api_key`
查询参数。语义详见
[runtime-api.md](runtime-api.md#工作台浏览器会话bootstrapcookiecsrf)）：

| 方法与路径 | 语义 |
| --- | --- |
| GET `/v1/threads/{id}/events/stream?since=N` | SSE 事件流：回放 + 实时 + 15s 心跳，16 连接上限 |
| GET `/v1/sessions` | REPL 会话列表（JSONL 首行 meta，updated_at 倒序，cwd 过滤） |
| GET `/v1/sessions/{id}/transcript` | 会话转录分页读取（id 仅 `[0-9a-zA-Z-]+`） |
| GET `/v1/usage/summary?days=N` | usage 聚合（`UsageStore.stats` 原样形状） |
| GET `/v1/approvals` | 挂起审批列表 |
| POST `/v1/approvals/{id}/decision` | 审批决策（仅 `approve`/`deny`） |
| GET `/` 与非 `/v1/*` | 静态资源 / SPA 回退 / 空态页 |

既有 threads / turns / tasks 端点见 [runtime-api.md](runtime-api.md)，不在此重复。

## 部署边界与安全

- 服务端硬绑定 `127.0.0.1`；无多用户认证、不做公网部署（与 MCP server 边界一致）
- 静态资源免鉴权（无用户数据的构建产物）；`/v1/*` 全走鉴权（外部 `x-api-key` 或
  bootstrap 换发的浏览器 cookie 会话，cookie 通道的变更请求另须 CSRF）
- SSE 的 `api_key` 查询参数会出现在本机浏览器历史（浏览器 `EventSource` 无法设置自定义
  请求头，这是唯一出口）；缓解是仅绑定 localhost 且该端点只读
- 会话转录端点返回用户对话正文（本机敏感数据），与 `nexuscli sessions` CLI 同暴露级别
- 审批挂起 300s 无决策按 deny 失败关闭；执行器把超时 deny 也记为 `approver="hitl"`
  （词表无 `approval-timeout` 值，桥在 `resolved_by` 里留存真相）

## 如实边界

- MVP 无前端单元测试（vitest 挂账）；质量门槛 = `tsc -b` 构建通过 + Python 侧端点测试 +
  真实进程 smoke
- `frontend/dist` 已入库但未打进 wheel（检出仓库或设置 `NEXUSCLI_WORKBENCH_DIST` 使用）
- Windows 下的前端构建不被 CI 覆盖（frontend job 仅 ubuntu）
- 截图待补（README 章节内为 HTML 注释占位）
- 审批挂起/解决的 SSE 推送未做（MVP 用轮询）；Web 端不提供 `approve_run` / `approve_auto`
