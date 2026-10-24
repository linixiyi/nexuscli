本文定稿工作台服务端两个特性：F2（SSE 事件流端点 + 静态资源托管）与 F6（Web 审批卡片的服务端语义）。全部设计建立在已读代码之上：现有 Runtime API 是标准库 `http.server.ThreadingHTTPServer` 实现（`src/nexuscli/runtime/api.py:12,75`），零 Web 框架依赖（`pyproject.toml:9-16`，`requires-python = ">=3.11"` 见 `pyproject.toml:6`），因此 SSE、静态托管与审批桥全部用标准库实现，零新增 Python 依赖。

## 业务逻辑

### F2：实时推送与 UI 分发

**现状（已核实）**：`nexuscli serve`（`src/nexuscli/entrypoints/cli.py:370-390`）启动 `RuntimeApiServer.serve_forever`：`ThreadingHTTPServer` 硬绑定 `127.0.0.1`（`api.py:75`），每个连接一个线程（stdlib `ThreadingHTTPServer` 自带 `daemon_threads=True`），路由集中在 `_handle` 的 if/elif 链（`api.py:105-159`），鉴权走 `_authorized`（`Authorization: Bearer` 或 `x-api-key`，`api.py:290-297`）。现有的 GET `/v1/threads/{id}/events` 不是流：`_send_events` 把 events 表全量行一次拼好、带 `content-length` 写完即断（`api.py:357-375`），前端只能轮询，看不到 turn 进行中的增量。

**F2 的业务语义**：给同一个 server 叠两个能力，不改变既有端点行为——

1. **SSE 事件流**：新增 GET `/v1/threads/{id}/events/stream`，把 events 表的新行实时推给浏览器，事件从「轮询快照」升级为「持续推送」。它是 API 面的一部分，随 `serve` 常驻可用，不需要专门开关。
2. **静态资源托管**：`serve --workbench` 新增开关，把 `frontend/dist` 映射到 `/`，浏览器打开即得工作台 UI。不开 `--workbench` 时行为与今天完全一致（静态路由不存在）。`--http` 选项维持现状：当前就被接收但未使用（`cli.py:381` 的 `_ = http`），不借本轮改变其语义。

绑定地址维持 `127.0.0.1` 硬编码（`api.py:75`），与 master「默认只绑定 localhost」及 MCP server 的部署边界一致；不做公网部署、不做多用户认证（本轮边界）。

### F6：从「无人可问」到「localhost 的人可答」

**现状（已核实）**：2026-10-18 审计收口 A4（`.spec/plans/archive/2026-10-18-closure-plan.md` 第一节）把 Runtime 任务接入了审批通道，落地形态是 fail-closed：Runtime 的 turn/task 构造 `QueryEngine` 时**不传** `approval_callback`（`api.py:168-176`、`api.py:264-273`），默认 None（`src/nexuscli/agent/query_engine.py:24`）；执行器链在无回调时一律拒绝——需要审批的工具调用返回 deny（`src/nexuscli/tools/executor.py:369-370`），写域守卫同样失败关闭（`src/nexuscli/tools/builtins.py:662-667`）。效果：Runtime 里 `write_file` 等不再静默落盘，但也没有任何「人」能点头，任务只能拿到被拒的工具结果。

**F6 的业务语义**：给 Runtime 的 turn/task 接上一个**真实的审批者**——localhost 上的 Web 用户——而不改执行器链的任何门序。工具调用需要人点头时，请求被挂起到内存登记表，Web 用户查询、批准或拒绝，决策沿原回调返回。由此三条不变量必须成立（设计见「数据流」「接口与边界」）：

- **同一门链**：permission deny 规则、hooks、写域守卫都发生在回调之前（`executor.py:136-151`、`builtins.py:627-660`），Web 决策只能回答已经到达回调的问题，不存在绕过面。
- **同一审计口径**：决策沿 `approval_callback` 返回后，审计由执行器照常落盘（`executor.py:231-240`、`executor.py:278-286`），approver 词表不变；Web 端不新增审计旁路。
- **Reason 行**：审批请求形态本就带 `description`（`executor.py:378-385`、`builtins.py:668-675`），REPL 渲染为 `Reason:` 行（`src/nexuscli/entrypoints/commands/approval.py:54-59`）；Web 卡片同样必须渲染 description，这是接口契约的一部分（验收钩子里有对应断言）。

Web 端**不提供** `approve_run`（本次 run 内免再问）与 `approve_auto`（切 auto 模式）：二者是 REPL 会话语义（`src/nexuscli/entrypoints/commands/approval_keys.py:12-16`、`src/nexuscli/tools/base.py:37` 的 `run_scope_approved`），Web MVP 只暴露 approve/deny 两个动词，避免 Web 一键放大信任范围。

### F4/F5 的服务端半边：会话与 usage 只读端点

（2026-10-25 复审修复：02 定稿时发现 REPL 会话转录与 usage 库没有任何 HTTP 端点，前端物理拿不到数据——本节把这两个读端点补进 01，作为 master F4/F5 的服务端半边 carrier。）

1. **会话列表** `GET /v1/sessions?limit=20&all=0`：扫描 `~/.nexuscli/sessions/*.jsonl`，读每个文件**首行** `type:"meta"` 记录（id/cwd/created_at/updated_at/model/provider/title/message_count/forked_from，与 `nexuscli sessions` 命令同数据源同口径），按 `updated_at` 倒序返回 `{"sessions": [...]}`。缺省只列 cwd 等于 serve 工作目录的会话（`meta.cwd` 与 `serve --cwd` 归一化后比较），`all=1` 列全部项目。损坏行跳过（与 `/history` 检索同一容错口径）。
2. **转录回放** `GET /v1/sessions/{id}/transcript?offset=0&limit=200`：按行返回该会话 JSONL 的消息记录 `{"messages": [...]}`（`type:"meta"` 之外的全部行，含 `role/content` 与工具调用记录；`type:"usage"` 行原样保留由前端决定呈现）。**id 安全校验**：只接受 `[0-9a-zA-Z-]+`，含路径分隔符或 `..` 一律 404（与 plan id 的文件名校验同取向）；文件不存在 404。
3. **usage 汇总** `GET /v1/usage/summary?days=7`：**直接复用已存在的 `UsageStore.stats(days)`**（`src/nexuscli/llm/usage_store.py:162-246`，无 HTTP 面），响应即其原样序列化：`{"days", "turns", "input_tokens", "output_tokens", "total_tokens", "total_cost": {"usd", "cny"}, "by_model": [{"model", "turns", "total_tokens", "total_cost"}], "by_source": [{"source", "turns", "total_tokens", "total_cost"}]}`（by_model/by_source 为数组；与 02 定稿的 `UsageStats` TS 类型逐字段一致）。聚合口径对齐 `/usage stats`。库不存在或为空时返回全零结构，不报错。
4. **边界**：三个端点全部要求 Bearer/x-api-key（标准鉴权门，无查询参数扩展）；只读 `~/.nexuscli/` 下的固定路径，不触碰工作区文件；零新增依赖（json/sqlite3 标准库）。如实边界：会话转录含用户对话正文，属于本机敏感数据——绑定 127.0.0.1 + 鉴权是仅有的两道门，与既有 `nexuscli sessions` CLI 同暴露级别，不额外脱敏（挂账）。

## 数据模型

### 事件与 SSE 帧（F2）

- 事件存储不变：SQLite `events` 表，`id integer primary key autoincrement` 全局自增（`api.py:398-411`），写入走 `_append_event`（`api.py:318-331`）。`id` 即断线重连游标（见「数据流」）。
- SSE 帧格式（新增，向后兼容现有快照端点）：

```
id: <events.id>
event: <type>
data: <payload JSON，单行>

```

  `type`/`payload` 就是现有列值（`api.py:357-375` 的取法），新增的只有 `id:` 行。心跳是 SSE 注释行 `: ping\n\n`，EventSource 与手工解析方都会忽略。
- 新代码提议放在 `src/nexuscli/runtime/` 内（如 `api.py` 新增私有方法 + 常量），帧拼装提取为模块级纯函数便于单测。

### 挂起审批（F6）

新增 `src/nexuscli/runtime/approvals.py`，核心是 `ApprovalBridge`，内存登记表（不落库，进程生命周期即审批生命周期）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | str | `approval_` 前缀，复用 `tasks.py:321-324` `_new_id` 的时间戳+随机后缀风格，避免 Windows 时钟粒度撞号 |
| `request` | dict | 执行器传入的 `{"tool_name","input","danger_level","description"}`，原样透传不加工 |
| `scope_type` / `scope_id` | str | `"thread"`+thread_id 或 `"task"`+task_id，供前端关联（见下） |
| `created_at` | str | UTC ISO，与 `tasks.py:313-314` 同口径 |
| `event` | threading.Event | 决策到达的唤醒信号 |
| `decision` | ToolDecision | `"approve"` / `"deny"`（`src/nexuscli/tools/base.py:11` 的子集） |
| `resolved_by` | str | `"web"` 或 `"timeout"`，仅供工作台 UI 展示，不进执行器审计 |

- **桥的上下文绑定**：执行器回调只带工具请求、不知道调用方（`executor.py:378-385`）。桥暴露 `callback_for(thread_id=..., task_id=...)` 返回绑定了上下文的回调闭包，`_run_turn`/`_run_task` 分别传入（改动点：`api.py:168-176`、`api.py:264-273` 给 `QueryEngine(...)` 增加 `approval_callback=self.approval_bridge.callback_for(...)`；`query_engine.py:30,46,59,70` 已把回调透传到 Agent/PlanExecuteAgent/Orchestrator，无需改动）。
- **审批挂起时任务的状态**：不新增任务状态。`TaskRecord` 状态机保持 queued/running/{completed,failed,canceled}（`src/nexuscli/runtime/tasks.py:9-10,13-26`），挂起审批期间任务就是 `running`：worker 心跳线程独立于任务执行保活 lease（`api.py:224-245`，30s 心跳、300s lease），审批等待不影响 lease 有效性。turn 侧同理：POST `/turns` 请求本来就阻塞在 `asyncio.run(self._run_turn(...))` 且持有 per-thread 锁（`api.py:126-129`），挂起期间同 thread 的后续 turn 继续排队——与「同 thread 并发 turn 串行化」的既有语义一致。
- **审计记录**：沿用 `AuditLog.record` 的固定字段 timestamp/tool_name/input/outcome/approver/cwd/trace_id（`src/nexuscli/policy/audit_log.py:29-57`，文件 0600）。approver 取值仍是现有词表 none/permission-rule/hook/hitl/run-scope/readonly-rule（六值）（`executor.py:110,143,170,197,232,251-262`）：Web 用户批准/拒绝经执行器落盘为 `approver="hitl"`（localhost 单人，语义为真）。

### 决策请求/响应体（F6）

- 查询：`GET /v1/approvals` → `{"approvals": [{id, tool_name, input, danger_level, description, scope_type, scope_id, created_at}]}`。
- 决策：`POST /v1/approvals/{id}/decision`，body `{"decision": "approve" | "deny"}`；其他动词一律 400。已解决/过期的 id 返回 404（不区分「从未存在」与「已解决」，避免二次决策的幂等歧义）。

## 数据流

### SSE 连接生命周期（F2）

新端点挂在**同一个** `ThreadingHTTPServer` 上：`_handle` 增加一个分支，流式响应占用当前请求线程直至连接结束（ThreadingHTTPServer 每连接一线程，天然支持长连接）。顺序：

1. **鉴权**：`_authorized`（`api.py:290-297`）当前只认 `authorization`/`x-api-key` 两个头，需要**一处显式扩展**：对 stream 路径额外接受 `api_key` **查询参数**——浏览器 `EventSource` 无法设置自定义请求头，这是让官方前端免自写 SSE 解析器的唯一出口。扩展仅限本端点，其余 `/v1/*` 维持只认两个头（验收钩子有负向断言锁住不外溢）；风险（key 出现在本机浏览器历史）如实写进 docs，缓解是绑定 127.0.0.1 + 该端点只读。
2. **Origin 校验**：带 `Origin` 头且非 loopback 一律 403，复用 MCP server 的 A3 先例（`src/nexuscli/mcp/server.py:110-126` 的 `_is_loopback_origin` 与 `:168-172` 的拒绝路径），把该函数提取到两处共用（边界：`mcp/server.py` 与 `runtime/api.py`）。
3. **并发上限**：`threading.BoundedSemaphore`（常量 `MAX_SSE_CLIENTS = 16`）。acquire 失败立即 503 + `retry-after: 5`，不排队——16 个并发流对 localhost 单用户绰绰有余，上限的目的是封住「每连接一线程」的线程数爆炸（ThreadingHTTPServer 本身不限连接数）。
4. **回放**：`since` 查询参数（整数，默认 0）起，查 `where thread_id = ? and id > ?  order by id`，逐帧写出（帧含 `id:` 行）。
5. **实时等待**：`_append_event`（`api.py:318-331`）在 insert 提交后对进程内 `threading.Condition` 做 `notify_all`；流线程 `cond.wait(timeout=15)` 醒来后**重新查库**取 `id > last` 的行。DB 是唯一事实源，Condition 只是消除轮询延迟的唤醒信号——唤醒丢不了事件（醒来按 last 重查），唤醒多余也无害（重查为空就继续等）。
6. **心跳**：`wait` 超时（15s 无新事件）写一行 `: ping\n\n`。
7. **断开检测**：写失败即断开——`BrokenPipeError`/`ConnectionResetError`/`OSError` 任一出现就清理退出；进流前对 socket 设 `settimeout(15)`，客户端半死（不读也不关）时写操作会在超时后抛 `TimeoutError` 而不是永久阻塞线程。退出时释放信号量槽位。
8. **停机**：`serve_forever` 的 `finally` 已有 `_stop` 事件（`api.py:77-80`）；流线程在每次循环检查 `_stop`，置位即关闭连接。

**断线重连语义——定稿选 `since` 游标**（二选一的取舍）：

- 选 `since` 的理由：① 重连必须由前端手工发起——`EventSource` 带不了 `x-api-key` 头，鉴权已经强制走 `api_key` 查询参数，自动重连反而会把无参 URL 重放全量；既然重连注定是手工的，游标就该显式。② `since` 对 curl/fetch/EventSource 一切客户端可用，Last-Event-ID 只在 EventSource 自动重连时存在。③ 游标就是 events 表自增主键（`api.py:398-411`），服务端按 `id > since` 重放，天然无洞。
- 代价（如实写）：浏览器 EventSource 的原生自动重连不带 `since` 会重放全量，因此前端约定为「出错即 close、携带最后收到的 `id:` 手工重开」；服务端仍输出 `id:` 行，未来若改用原生重连只需服务端加读 `Last-Event-ID` 头（作为 `since` 缺省值），帧格式零改动。
- 首次连接 `since` 缺省 0 = 从头回放，与现有快照端点「返回全量」的语义一致（`api.py:357-375`），刷新后仍可继续观察（master F4 的要求由此承接）。

### 静态资源数据流（F2）

- **目录映射**：dist 根 = `<cwd>/frontend/dist`（`cwd` 即 `serve --cwd`，`cli.py:382`）。如实校准现状：仓库今天既无 `frontend/` 目录、git 也未跟踪任何 dist 文件，master 的表述是「允许提交」的假设（master.md:32,76）而非既成事实；且第 2 轮实现顺序 F2 先于 F3，F2 落地时 dist 必然不存在——因此**空态页是 F2 交付时的默认形态**，F3 产出 `frontend/dist` 入库后 `/` 自动切换为托管，服务端代码无需改动。wheel 安装形态同样走空态页。`NEXUSCLI_WORKBENCH_DIST` 环境变量可覆盖。把 dist 打进 wheel 挂账（见「接口与边界」）。
- **GET `/`**：dist 存在 → `dist/index.html`；不存在 → 内嵌字符串的空态 HTML（零依赖），内容为「工作台 UI 未构建」+ 构建指引（`cd frontend && npm install && npm run build`，具体命令以 02 前端文档定稿为准）+ 提示 `/v1/*` API 不受影响。
- **其他 GET（非 `/v1/*` 前缀）**：先 `urlsplit` 取 path、`posixpath.normpath` 归一并**拒绝含 `..` 段或绝对路径的输入**（第一道）；再 `(dist_root / rel).resolve()` 后要求 `resolved.is_relative_to(dist_root.resolve())` 且 `is_file()`（第二道，防符号链接逃逸；Python ≥3.11 保证 `is_relative_to` 可用）。通过则按 Content-Type 表回文件，否则：无后缀的路径回退 `index.html`（SPA 路由），带后缀的 404。`/v1/*` 永不落入静态分支。
- **Content-Type**：stdlib `mimetypes.guess_type` + 显式覆盖表（js/mjs→`text/javascript`、css、html、json、svg、png、ico、map、woff2、wasm），文本类加 `charset=utf-8`——Windows 注册表污染 mimetypes 时覆盖表兜底。缓存策略 MVP 一律 `cache-control: no-store`（localhost，换取「改了就生效」）。
- **鉴权**：静态资源不要求 api_key（它们是无用户数据的构建产物；UI 自己向用户要 key 再调 `/v1/*`）。

### 审批数据流（F6）

1. 工具调用进入执行器门链：permission deny 规则 → PreToolUse/PermissionRequest hooks → 只读直通/allow 规则/hitl_mode 判定 → 才轮到 `approval_callback`（`executor.py:136-213`、`executor.py:346-388`）。**能走到回调的，只剩「真的需要人点头」的调用**；deny 规则与 allow 语义在回调之前尘埃落定，Web 端物理上碰不到（这就是「写域守卫与 permissions allow 不得被 Web 绕过」的机制保证：写域守卫复用同一回调通道且自带 allow 短路，`builtins.py:653-675`；Web 决策无法注入 allow 规则，也无法覆盖 deny 规则）。
2. 回调（`bridge.callback_for(...)` 返回的闭包）登记 `PendingApproval`，`event.wait(timeout=APPROVAL_TIMEOUT_SECONDS=300)` 阻塞当前 turn/task 的执行线程。
3. 前端轮询 `GET /v1/approvals`（MVP 用 2s 轮询；审批挂起/解决的 SSE 推送挂账，见「接口与边界」），渲染卡片：工具名、danger_level、单行参数摘要、**description 作为 Reason 行**（缺失时显示 `(no reason provided)` 并视为服务端契约缺陷，见验收钩子）。
4. 用户点批准/拒绝 → `POST /v1/approvals/{id}/decision` → 桥置 `decision`、`set()` event → 回调返回 ToolDecision → 执行器照常走后续：deny/skip 记审计 `outcome=deny, approver="hitl"`（`executor.py:231-240`）；approve 的非只读工具执行后记 `outcome=allow/error, approver="hitl"`（`executor.py:261-262`、`executor.py:278-286`，审计开关 `features.audit_log`、路径 `policy.audit_log_path` 默认 `~/.nexuscli/audit.jsonl`，`src/nexuscli/config.py:74,99`）。
5. **超时**：300s 无决策 → 回调返回 `deny`（失败关闭，与 AGENTS.md「无审批回调时按失败关闭」同取向），保证 worker 槽位不被两张没人看的卡片占死（默认 2 个 worker，`api.py:36`）。如实披露一处口径偏差：执行器把超时 deny 也记为 `approver="hitl"`（`executor.py:231-233` 对回调返回的 deny/skip 不区分来源），而超时不是人的决定；桥在 `resolved_by="timeout"` 里留存真相供 UI 展示，给审计词表增加 `approval-timeout` 值需要动 `executor.py`，挂账。
6. **取消语义**：任务的取消检查只发生在引擎事件间隙（`api.py:276-277`），审批挂起属于单次工具调用内部，与今天「长 bash 调用无法被中途取消」同构；挂起期间取消在决策落定后的下一个事件间隙生效。前端引导：先 deny 再 cancel。

## 接口与边界

### 新增端点一览（路由都走 `_handle` 的 if/elif 链；鉴权门需要两处显式改动，见下）

`_handle` 的第一件事是**无条件** `_authorized`（`api.py:106-108`），先于任何路由分支——因此除路由本身外，还有两条鉴权改动必须写明：① 静态路由（仅 `--workbench`）在鉴权门**之前**分流（`_handle` 入口先判静态 GET），否则 `GET /` 会先撞 401；② SSE 的 `api_key` 查询参数通过扩展 `_authorized` 实现，且仅对 stream 路径生效。

| 方法与路径 | 鉴权 | 语义 | 错误 |
| --- | --- | --- | --- |
| GET `/v1/threads/{id}/events/stream?since=N[&api_key=...]` | Bearer/x-api-key，或 api_key 查询参数（`_authorized` 仅对本路径扩展） | SSE 流，回放+实时+心跳 | 401 未鉴权；403 跨源 Origin；404 thread 不存在（`_thread_exists`，`api.py:309-312`）；503 流槽满 |
| GET `/v1/sessions` | Bearer/x-api-key | 会话列表（JSONL 首行 meta，updated_at 倒序） | 401 |
| GET `/v1/sessions/{id}/transcript` | Bearer/x-api-key | 会话转录分页读取；id 仅 `[0-9a-zA-Z-]+` | 401；404 非法 id/不存在 |
| GET `/v1/usage/summary?days=N` | Bearer/x-api-key | `UsageStore.stats(N)` 原样序列化（usage.db 近 N 天聚合） | 401 |
| GET `/v1/approvals` | Bearer/x-api-key | 列出挂起审批 | 401 |
| POST `/v1/approvals/{id}/decision` | Bearer/x-api-key；Origin 语义同 MCP A3：带 Origin 且非 loopback → 403，无 Origin（curl 等非浏览器客户端）放行 | `{"decision":"approve"\|"deny"}`，唤醒回调 | 400 非法动词；404 未知/已解决 |
| GET `/`、GET 非 `/v1/*` 路径（仅 `--workbench`） | 无（鉴权门之前分流，`api.py:106-108` 的例外） | dist 文件 / SPA 回退 / 空态页 | 404 |

- 既有端点全部不变（含快照 GET `/v1/threads/{id}/events`，`api.py:131-136`）；`worker` 子命令（`api.py:82-91`）不暴露 HTTP，不受影响。
- CLI：`runtime_serve` 增加 `--workbench` 开关（默认 False，`cli.py:370-390`），开启时打印 `Workbench: http://127.0.0.1:{port}/`；端口占用报错继续复用 `_serve_or_exit`（`cli.py:762-782`）。
- 可测性小重构：`serve_forever` 把 `ThreadingHTTPServer` 构造提取为 `_build_http_server()`，支持 port=0 注入，测试口径照 `tests/test_mcp_http_security.py:24-41` 的真端口 fixture。
- 测试凭据沿用 `tests/test_runtime.py:11-13` 的 `_runtime_test_key` 占位惯例。

### 边界与挂账（本轮明确不做 / 无法在本轮定稿）

- 不做：多用户认证与公网部署（绑定 127.0.0.1 不变）；WebSocket（SSE 够用且零依赖）；改动 REPL/TUI 审批交互；给审计词表增加 identity 字段或 `approval-timeout` approver 值（动 `executor.py`/`audit_log.py`，超出 F6 边界，超时偏差已在「数据流」第 5 步如实披露）。
- 不提供：Web 端 `approve_run` / `approve_auto`（理由见「业务逻辑」）；决策端点不接受任何其他动词。
- 挂账：① dist 打进 wheel（当前依赖仓库检出或 `NEXUSCLI_WORKBENCH_DIST`）；② 审批挂起/解决的 SSE 推送（MVP 用轮询，F7 做 Web 发起 turn 时再评估并入事件流）；③ `api_key` 查询参数之外更体面的 SSE 鉴权（如一次性短时 token 或 cookie）；④ `APPROVAL_TIMEOUT_SECONDS`/`MAX_SSE_CLIENTS` 是否升级为 config 项（MVP 为模块常量）。
- 依赖约束复核：以上全部只用 stdlib（`http.server`、`threading`、`mimetypes`、`posixpath`、`uuid`、`secrets`），`pyproject.toml:9-16` 不动。
- 任务派生提示（供 /spec:new 建包）：① `_build_http_server` 提取 + Origin 共用函数提取（纯重构，先行为不变验证）；② SSE 端点（回放→实时→心跳→断开，逐段有测试）；③ 静态托管（映射/穿越/空态/Content-Type）；④ `--workbench` CLI 开关；⑤ `approvals.py` 桥 + 两端点 + `_run_turn`/`_run_task` 接线；⑥ **F4/F5 服务端读端点**（`/v1/sessions`、`/v1/sessions/{id}/transcript`、`/v1/usage/summary`——id 校验、cwd 过滤、usage 聚合口径与 /usage stats 对齐；02 的会话视图与 usage 视图以此为本节契约消费）；⑦ 文档（docs/workbench.md 归 F8，本轮只保证 README Runtime API 章节不与新端点矛盾）。

## 验收钩子

pytest 环境由 `tests/conftest.py:19-37` 的 autouse fixture 重定向 HOME 与 USERPROFILE，以下新测试不自行绕开（审计日志、runtime.db、tasks.db 全部落进临时目录）。

**新增测试文件与用例**（命令：`uv run python -m pytest -q tests/test_runtime_sse.py tests/test_runtime_static.py tests/test_runtime_approvals.py`，期望全绿）：

- `tests/test_runtime_sse.py`（SSE 流）：
  - 帧拼装纯函数：给定 `(id, type, payload)` 输出含 `id:`/`event:`/`data:` 三段与空行结尾；payload 为单行 JSON。
  - 真端口 fixture（照 `test_mcp_http_security.py:24-41`，`RuntimeApiServer(port=0)` + `_build_http_server`）：连接 `?since=0` 先收到已落库事件（断言帧序与 `api.py:318-331` 写入顺序一致）；连接后由另一线程 `_append_event` 追加新事件，断言 15s 内收到对应帧（唤醒路径）；`?since=<最后id>` 重连只收到之后的事件（断线重连语义）。
  - 无 `api_key` 查询参数且无头 → 401；带 `Origin: https://evil.example` → 403；未知 thread → 404。
  - 查询参数鉴权不外溢：`POST /v1/threads?api_key=...` 与 `GET /v1/tasks?api_key=...` 仍 401（`_authorized` 的查询参数扩展仅对 stream 路径生效，`api.py:290-297` 的其余调用点不变）——锁住整个 API 的鉴权面不被顺手放宽。
  - 占满 16 个流后第 17 个连接 → 503。
- `tests/test_runtime_static.py`（静态托管）：
  - dist 存在：`GET /` 回 index.html、`content-type: text/html; charset=utf-8`；`.js`/`.css`/`.svg` 文件的 Content-Type 符合覆盖表（在 tmp_path 伪造 dist 树，`NEXUSCLI_WORKBENCH_DIST` 指向它）。
  - 路径安全：`GET /../etc/passwd` 与 URL 编码变体、指向 dist 外的符号链接 → 404；`GET /nonexistent`（无后缀）→ 回 index.html（SPA 回退）；`GET /missing.js` → 404；`GET /v1/threads` 永不落入静态分支。
  - dist 缺失：`GET /` 回空态页，断言正文含「未构建」与「npm」构建指引字样；`--workbench` 未开启时 `GET /` → 404（行为与今天一致）。
- `tests/test_runtime_approvals.py`（审批语义）：
  - 桥单元：`callback_for(task_id=...)` 在另一线程调用 → `GET /v1/approvals` 列出该项且 `description` 非空；`POST .../decision {"decision":"deny"}` → 回调返回 `"deny"`，二次决策同 id → 404；body 用 `"allow"` 等非法动词 → 400；不决策等超时（测试里以参数注入短超时）→ 返回 `"deny"` 且 `resolved_by=="timeout"`。
  - decision 端点 Origin 语义（锁住 A3 口径而非「必须携带 loopback Origin」）：带 `Origin: https://evil.example` 的决策 POST → 403 且桥登记表不变；无 Origin 头的同请求（`http.client` 缺省不发 Origin）→ 正常决策（`mcp/server.py:168-171`：Origin 缺省放行）。
  - 执行器集成（口径照 `tests/test_tool_approval_matrix.py:50-92` 的 write_file 用例，不依赖 LLM）：`ToolExecutor` + `approval_callback=bridge.callback_for(...)`，write_file → 桥挂起 → decision approve → 文件真实落盘，且审计行 `outcome="allow"`、`approver="hitl"`（读 conftest 重定向后的 audit.jsonl，参照 `tests/test_run_scope_approval.py:221` 的读法）；decision deny → 文件不存在、审计 `outcome="deny"`。
  - 不可绕过：配置 permissions deny 规则时同 payload 直接到 deny 结果，`GET /v1/approvals` 始终为空（门序在回调之前，`executor.py:136-151`）。
  - hitl_mode="never"（`config.py:74` 默认是 auto）：回调同样不被触达（桥登记表始终为空），**但 write_file 会被自动放行、文件真实落盘**——这是仓库既有语义，不是 fail-closed：`executor.py:367-368` 对 requires_approval 工具在 never 模式直接 approve，`docs/mcp.md:64` 明文「无需审批的完整工具能力」。断言对象是「桥登记表为空 + 文件已写入」；**不要**照抄 `tests/test_fs_scope.py:490-505`（`test_never_mode_fails_closed_without_prompting`）的 fail-closed 断言——那条先例测的是写域守卫（`builtins.py:662-667` 在 never 模式确实拒绝域外写），对执行器的 requires_approval 工具不成立，照抄会得到恒失败测试，进而误改 executor 门序。
  - Reason 契约：对 `executor.py:378-385` 与 `builtins.py:668-675` 两个调用点构造的请求，断言 `description` 键存在且非空（锁住「审批提示带 Reason 行」不回退）。

**lint**：`uv run ruff check .` 与 `uv run ruff format --check .` 全绿（新文件遵守现有 noqa 惯例，如 stdlib 命名 `do_GET` 的 `# noqa: N802`，`api.py:66-70`）。

**smoke（真实进程，人工执行一次并入任务验证记录）**：

```bash
NEXUSCLI_RUNTIME_API_KEY=dev-key uv run nexuscli serve --workbench --port 8080 &
# API 冒烟沿用 docs/runtime-api.md:14-47 的 curl 序列创建 thread 并发 turn，然后：
curl -sS -N -H 'x-api-key: dev-key' 'http://127.0.0.1:8080/v1/threads/<id>/events/stream?since=0'
# 期望：立即回放既有事件；turn 进行中 message.delta 逐帧到达；空闲 15s 出现 ": ping"
curl -sS http://127.0.0.1:8080/ | head -5   # F2 交付时 frontend/dist 尚未由 F3 产出，预期是空态页（正文含「未构建」）；F3 入库 dist 后为 doctype 文档（大小写不敏感比较：Vite react-ts 模板产出小写 `<!doctype html>`，断言按 lower() 后比较，口径见 02 勘误）
```

期望：`--workbench` 打印 `Workbench: http://127.0.0.1:8080/`；不带 `--workbench` 时 `/` 为 404、`/v1/*` 与今天完全一致。
