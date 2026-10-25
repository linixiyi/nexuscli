# Runtime API

> 本文是 [README](../README.md)「📡 Runtime API」章节的完整版。

NexusCLI 内置轻量 Runtime API，适合外部系统接入线程、turn、事件和后台任务。

启动服务：

```bash
NEXUSCLI_RUNTIME_API_KEY=dev-key \
uv run nexuscli serve --http --port 8080
```

创建线程：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/threads \
  -H 'x-api-key: dev-key'
```

发送 turn：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/threads/<thread_id>/turns \
  -H 'content-type: application/json' \
  -H 'x-api-key: dev-key' \
  -d '{"message":"总结这个项目"}'
```

读取事件：

```bash
curl -sS http://127.0.0.1:8080/v1/threads/<thread_id>/events \
  -H 'x-api-key: dev-key'
```

创建并查看后台任务：

```bash
curl -sS -X POST http://127.0.0.1:8080/v1/tasks \
  -H 'content-type: application/json' \
  -H 'x-api-key: dev-key' \
  -d '{"message":"后台总结这个仓库","mode":"plan"}'

curl -sS http://127.0.0.1:8080/v1/tasks \
  -H 'x-api-key: dev-key'
```

也可以只启动队列消费者，不暴露 HTTP：

```bash
uv run nexuscli worker --workers 2 --cwd .
```

任务队列按项目目录隔离；worker 使用 SQLite 原子事务领取任务，并通过 lease/heartbeat 恢复崩溃任务。运行中取消会阻止 worker 把迟到结果重新覆盖为 completed。

## 端点清单

<!-- BEGIN GENERATED: endpoint-table (do not edit manually) -->
> 本节由 `scripts/generate_endpoint_docs.py` 从 `src/nexuscli/runtime/api.py` 的 `ROUTE_MANIFEST` 生成，**请勿手改**。

| 方法 | 路径 | 鉴权 | 语义 |
| --- | --- | --- | --- |
| GET | `/` | 免鉴权（仅 --workbench） | 工作台静态资源 / SPA 回退 / 空态页（匹配非 /v1/* 的 GET） |
| POST | `/v1/workbench/bootstrap` | 一次性口令 | 口令一次性换发 HttpOnly cookie 会话与 x-csrf-token |
| POST | `/v1/threads` | api_key / cookie+csrf | 创建线程，返回 {id} |
| POST | `/v1/threads/{thread_id}/turns` | api_key / cookie+csrf | 同步执行一个 turn（mode=react/plan/team），完成前阻塞 |
| GET | `/v1/threads/{thread_id}/events/stream` | api_key / cookie / api_key 查询参数 | SSE 事件流：回放 + 实时 + 15s 心跳（?since=N 续传，16 连接上限） |
| GET | `/v1/sessions` | api_key / cookie | REPL 会话列表（?limit、?all 查询参数，按 cwd 过滤） |
| GET | `/v1/sessions/{session_id}/transcript` | api_key / cookie | 会话转录分页读取（?offset、?limit 查询参数） |
| GET | `/v1/usage/summary` | api_key / cookie | 用量聚合汇总（?days 查询参数） |
| GET | `/v1/threads/{thread_id}/events` | api_key / cookie | 线程事件全量列表（text/event-stream 帧文本，按 id 升序） |
| GET | `/v1/approvals` | api_key / cookie | 挂起审批列表 |
| POST | `/v1/approvals/{approval_id}/decision` | api_key / cookie+csrf | 审批决策（decision=approve/deny，同动词幂等重放） |
| GET | `/v1/workbench/permissions` | api_key / cookie | 会话权限快照（含 expected_revision） |
| PATCH | `/v1/workbench/permissions` | api_key / cookie+csrf | 会话权限变更（expected_revision 乐观锁，即时重判挂起审批） |
| POST | `/v1/tasks` | api_key / cookie+csrf | 创建后台任务（mode=react/plan/team） |
| GET | `/v1/tasks` | api_key / cookie | 后台任务列表 |
| GET | `/v1/tasks/{task_id}` | api_key / cookie | 后台任务详情 |
| POST | `/v1/tasks/{task_id}/cancel` | api_key / cookie+csrf | 取消后台任务（运行中取消不被迟到结果覆盖） |

<!-- END GENERATED: endpoint-table -->

## 工作台浏览器会话（bootstrap/cookie/csrf）

上面的 curl 示例全部走外部自动化通道（`x-api-key` 头）。`--workbench` 启动的可视化
工作台另有一套浏览器会话鉴权语义（实现见 `src/nexuscli/runtime/api.py` 的
`_handle`/`_handle_bootstrap` 与 `src/nexuscli/runtime/websession.py`）：

- **一次性 bootstrap 口令**：服务启动生成一次性口令并在终端打印
  `http://127.0.0.1:<port>/#bootstrap=<token>` 一键链接；浏览器打开后前端读出 URL
  片段中的口令（随即抹除，token 不留地址栏），`POST /v1/workbench/bootstrap` 一次性
  换发——口令错误或重放一律 403。该端点免 cookie/key 门：口令本身即凭证。
- **HttpOnly cookie 会话**：换发成功即 `Set-Cookie`（HttpOnly，JS 不可读；
  SameSite=strict，跨站不携带），之后浏览器请求同源自动携带 cookie 作为凭证。
- **x-csrf-token**：仅凭 cookie 通过会话门的变更请求（POST）必须另带 `x-csrf-token`
  头（bootstrap 响应体返回，前端存内存不落 localStorage）——cookie 自动携带挡不住
  跨源伪造，csrf 补上这一刀；api_key 通道豁免（外部程序没有 cookie，凭证本身就是
  凭据）。
- **Origin 白名单**：`/v1/*` 上任何带 `Origin` 头且来源非本机回环的请求一律 403，
  且先于凭证判断（无凭证也拒绝跨源探测）；无 `Origin` 的 curl 等非浏览器客户端
  不受影响。
- **三通道三选一**：会话门接受 `x-api-key`/`Bearer` 头、SSE 的 `api_key` 查询参数
  （仅 SSE 路径）、HttpOnly cookie 三者之一。外部通道原样保留，本文所有 curl 示例
  无需改动。

会话生命周期 = 进程生命周期：不持久化、不刷新，重启即新一轮口令；静态资源免鉴权
（构建产物无用户数据）。功能面与端点清单见 [workbench.md](workbench.md)。
drift line
