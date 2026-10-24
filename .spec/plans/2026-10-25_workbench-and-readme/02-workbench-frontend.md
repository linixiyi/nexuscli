本文定稿工作台前端四个特性：F3（React + Vite 前端工程）、F4（会话列表 + 转录回放）、F5（任务面板 + usage 统计）、F7（Web 端发起 turn）。接口契约严格以 01 文档「接口与边界」为准（`.spec/plans/2026-10-25_workbench-and-readme/01-workbench-server.md`），前端不定义、不改名任何端点；已核实的现状是：仓库今天既无 `frontend/` 目录也无任何 `package.json`（`find` 全仓确认），根 `.gitignore:9` 的 `dist/` 规则会忽略 `frontend/dist`，因此「构建产物提交入库」需要一条显式 gitignore 例外，这是 F3 任务的一部分。F4/F5 所需的服务端读端点（sessions/transcript/usage）已由 01「F4/F5 的服务端半边」节定稿（2026-10-25 复审收口），前端照契约消费，无占位降级口径。

## 业务逻辑

### F3：前端工程的定位与形态

工作台 UI 是**纯静态 SPA**：构建后由 01 契约的静态托管分发（`--workbench` 开启时 `GET /` 与非 `/v1/*` GET 映射 `frontend/dist`，静态资源不要求 api_key，01「数据流·静态资源数据流」节）；开发时经 Vite dev server 的 proxy 同源转发到 Runtime API。技术栈定稿为 **Vite + React + TypeScript**（master 已确认决策），依赖面刻意最小：

- 运行时依赖只有 `react`、`react-dom`；开发依赖 `vite`、`@vitejs/plugin-react`、`typescript` 及两个 `@types` 包。
- **不引入**：路由库（单页 tab 导航，hash 定位视图）、状态管理库（hooks + 模块级状态）、UI 组件库、markdown 渲染库（助手正文用 `white-space: pre-wrap` 纯文本渲染；TUI 侧的 CommonMark 硬换行坑是 rich 的，不适用于 web，见 AGENTS.md「渲染与模型网关」节的对照）、前端测试框架（vitest 不进本轮，master 边界原文；类型检查由 `tsc -b` 承担）。
- 前端依赖只进 `frontend/package.json`，`pyproject.toml:9-16` 不动（零 Python 依赖约束不变）。

**Node 版本定稿**：`engines.node` = `^20.19.0 || >=22.12.0`——与 npm 实测的 `vite@8.3.5` engines 完全一致（2026-10-25 实测；本机 `node v24.13.1`、`npm 11.19.1` 已验证可用）。注意这不是简单 `>=20.19`：Node 21 与 22.0-22.11 不在支持线内。依赖版本由 `package-lock.json` 锁定（本文不虚构具体版本号；实测 react-ts 模板为 react 19 / vite 8 一代）；构建本身就是版本要求的验收（Node 不满足时 `npm run build` 直接失败）。

**构建产物入库**：`frontend/dist` 提交 git，先例是仓库已跟踪的 `site/` 生成内容（`git ls-files site` 实测含 `index.html`、`site.js`、`docs/*.html` 等）。落地要点：根 `.gitignore:9` 的 `dist/` 会命中 `frontend/dist`，必须在根 `.gitignore` 追加一行 `!frontend/dist/`（否定规则写在 `dist/` 之后即生效，gitignore 语义为后匹配者胜）；`frontend/.gitignore` 忽略 `node_modules/`。心智与 `site/` 一致：每次构建后随实现改动一起 commit。

**两种运行方式**（写进 README 工作台章节的口径，F8 落笔）：

- 开发：`cd frontend && npm ci && npm run dev`（Vite 默认 5173 端口，`/v1` 经 proxy 转发到 Runtime API）。
- 使用：`cd frontend && npm ci && npm run build` 后 `NEXUSCLI_RUNTIME_API_KEY=... uv run nexuscli serve --workbench --port 8080`，浏览器打开 `http://127.0.0.1:8080/`；运行时不需要 Node。

### F4：会话视图的业务语义

会话视图回答「这个项目发生过什么」：列出可回放的会话/线程，选中后呈现**消息与工具调用的完整时间线**，刷新页面后仍能继续观察进行中的 turn。数据源有两块，范围如实分清：

1. **Runtime 线程转录**（01 契约内，可直接交付）：Runtime API 的线程事件（`runtime.db` 的 events 表，`src/nexuscli/runtime/api.py:402-411`）。入口是「已知道的程 ID」：本浏览器发起过的线程（F7 落下时写入 localStorage 记忆）与手动输入的线程 ID。转录回放统一走 01 契约的 SSE 流 `since=0` 全量回放——刷新即重新回放，这是特性而不是成本。
2. **REPL 会话转录**（`~/.nexuscli/sessions/<id>.jsonl`，README「💾 会话与恢复」节所述）：磁盘格式已定稿（`src/nexuscli/session/store.py`），HTTP 面由 01「F4/F5 的服务端半边」节定稿——`GET /v1/sessions`（列表）与 `GET /v1/sessions/{id}/transcript`（分页转录），前端照契约消费（类型见「数据模型」，fetch 层在 `api/sessions.ts`）。交付口径：会话列表 + 转录回放完整交付，无占位降级。

### F5：任务面板 + usage 统计的业务语义

- **任务面板**：呈现后台任务的全生命周期——`GET /v1/tasks` 列表、`GET /v1/tasks/{id}` 详情、`POST /v1/tasks/{id}/cancel` 取消（三个既有端点，`api.py:137-153`）。状态机口径是 TaskRecord 的 queued/running/{completed,failed,canceled}（`src/nexuscli/runtime/tasks.py:9-10`），取消只在 queued/running 上可用（`tasks.py:156-172` 的 update 条件）。任务按项目目录隔离（scope 字段，`tasks.py:26`；`docs/runtime-api.md:55`），面板展示 scope 以便用户理解「为什么看不到别的项目的任务」。
- **usage 统计**：数据源是 `~/.nexuscli/usage.db` 的 `turn_usage` 表（`src/nexuscli/llm/usage_store.py:82-96`），服务端聚合函数已存在——`UsageStore.stats(days)`（`usage_store.py:162-246`）——HTTP 面由 01 定稿为 `GET /v1/usage/summary?days=N`（**原样序列化 `UsageStore.stats` 返回值**，响应形状即本文 `UsageStats` 类型）。本轮交付视图组件、TS 类型与交互（天数窗口切换、汇总卡 + 两张拆分表）与 fetch 层（`api/usage.ts`），无挂账；README「💰 模型、Token 与费用」节的口径（成本以供应商返回的实际 Token 为准、未配价格时成本恒为 0）在视图空态文案中如实转述。

### F7：Web 端发起 turn 的业务语义

输入框提交 prompt → `POST /v1/threads`（需要时建线程，`api.py:113-115`）→ `POST /v1/threads/{id}/turns`（`api.py:116-130`）→ 经 01 契约的 SSE 流实时观察流式输出与工具调用。三条既有服务端语义直接决定前端交互形态：

1. **turn POST 是同步的**：请求阻塞到整个 turn 结束才返回 200（`api.py:129-130` 的 `asyncio.run(self._run_turn(...))`，响应体含 `total_tokens`，`api.py:199-204`）。所以前端把 POST 当「后台提交」发出、立即进入 SSE 观察，POST 的最终返回只作终态兜底。
2. **同线程 turn 串行**：第二个 turn 会在 per-thread 锁上排队（`api.py:126-129`），UI 在流式期间禁用输入框，避免用户误以为并发提交。
3. **审批挂起会暂停事件流**：F6 落地后 `_run_turn` 携带审批桥回调（01「数据模型·挂起审批」节），需要人点头的工具调用让 turn 停在当前帧直到决策或 300s 超时 deny（01「数据流·审批数据流」第 2/5 步）。工作台外壳因此常驻审批轮询（`GET /v1/approvals`，2s，01 定的节奏），审批卡片渲染 **description 作为 Reason 行**，只有 approve/deny 两个动词（01「业务逻辑·F6」：Web 不提供 approve_run/approve_auto）。

## 数据模型

### frontend/ 目录布局（定稿）

```
frontend/
  package.json          # 见下「脚本与依赖」
  package-lock.json     # 首次 npm install 生成并提交（npm ci 依赖锁文件，缺失直接报 EUSAGE）
  vite.config.ts        # dev proxy /v1 → Runtime API
  tsconfig.json         # strict: true
  index.html            # <title>NexusCLI Workbench</title>，唯一入口
  .gitignore            # node_modules/（根 .gitignore 无此条，已实测）
  src/
    main.tsx            # 挂载 App
    App.tsx             # 外壳：key 门 + tab 导航（会话/任务/用量）+ 审批卡片区
    api/
      client.ts         # fetch 封装：附 x-api-key 头；错误归一为 {status, message}
      types.ts          # 全部 TS 类型（对齐后端形状，见下）
      threads.ts        # createThread / postTurn / fetchEventsSnapshot（降级用）
      tasks.ts          # listTasks / getTask / cancelTask
      approvals.ts      # listApprovals / postDecision（形状照 01 契约）
      usage.ts          # fetchUsageSummary（01 契约 /v1/usage/summary，= UsageStore.stats 原样）
      sessions.ts       # listSessions / fetchTranscript（01 契约 /v1/sessions、/v1/sessions/{id}/transcript）
    sse/
      useEventStream.ts # EventSource hook：since 游标 + 手工重连 + 错误分类
    state/
      apiKey.ts         # localStorage 读写（键：nexuscli.workbench.apiKey）
      localThreads.ts   # 本浏览器创建的线程记忆（键：nexuscli.workbench.threads）
    views/
      SessionsView.tsx  # 会话/线程列表（服务端枚举 + 本地记忆 + 手动 ID）
      TranscriptView.tsx# 转录回放（SSE 驱动的时间线）
      TasksView.tsx     # 任务面板
      UsageView.tsx     # usage 统计
      Composer.tsx      # F7 输入框 + mode 选择（react/plan/team）
    components/
      Timeline.tsx      # 事件帧 → turn 分组条目的归并渲染
      MessageEntry.tsx  # 用户/助手消息条目（pre-wrap 文本）
      ToolCallCard.tsx  # 工具调用/结果卡片（is_error 红色描边）
      ApprovalCard.tsx  # 审批卡片（description 作 Reason 行）
      StatusPill.tsx    # 任务状态/连接状态徽标
    styles.css
```

### package.json 脚本与依赖（定稿）

- scripts：`dev` → `vite`；`build` → `tsc -b && vite build`；`preview` → `vite preview`。
- dependencies：`react`、`react-dom`。devDependencies：`vite`、`@vitejs/plugin-react`、`typescript`、`@types/react`、`@types/react-dom`。
- `engines.node` = `^20.19.0 || >=22.12.0`（见「业务逻辑」Node 版本定稿）。**lockfile 是工程的一部分**：首次以 `npm install` 生成 `package-lock.json` 并提交入库，其后构建一律 `npm ci`——`npm ci` 无锁文件时直接失败，F3 脚手架任务须把「lockfile 已生成并提交」列为完成条件之一。
- `build` 的 `outDir` 用 Vite 默认 `dist`，不另配。

### vite.config.ts（定稿）

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/v1": {
        target: process.env.NEXUSCLI_RUNTIME_ORIGIN ?? "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },
});
```

dev 模式**必须**走 proxy：`EventSource` 与 fetch 都请求同源的 `/v1/...`，而 stdlib server 不发任何 CORS 头（通读 `api.py:1-474` 确认无 `Access-Control-*` 输出），跨源直连不可行。proxy 原样转发查询串（含 SSE 的 `api_key` 参数）与响应流（http-proxy 流式透传，SSE 不断帧）。

### TS 类型（对齐后端真实形状）

```ts
// SSE 帧（01 契约：id:/event:/data: 三段；data 为单行 JSON）
type ThreadEventFrame = { id: number; type: string; payload: Record<string, unknown> };

// 事件 payload 字段（写入点均已读过）：
//   thread.created  {"id"}                                   api.py:306
//   turn.started    {"message","mode"}                       api.py:166
//   message.delta   {"text"}                                 api.py:185
//   tool_call       {"name","input"}                         agent.py:449 → api.py:187
//   tool_result     {"name","result","is_error"}             agent.py:467 → api.py:187
//   error           {"error"}（值为 str(exc)）                api.py:186-189
//   done            {"total_turns","total_tokens",...}        agent.py:519
//   turn.completed  {"text","mode","total_tokens"}            api.py:194-198

type TaskRecord = {   // tasks.py:13-26 的 asdict 形状（api.py:146 原样返回）
  id: string; prompt: string; status: "queued" | "running" | "completed" | "failed" | "canceled";
  created_at: string; updated_at: string; result: string | null; error: string | null;
  mode: string; attempts: number; worker_id: string | null;
  lease_expires_at: string | null; scope: string;
};

type SessionMeta = {  // store.py:31-41，REPL 会话元信息（01 契约 GET /v1/sessions 的条目形状）
  id: string; cwd: string; created_at: number; updated_at: number;
  model: string; provider: string; title: string; message_count: number; forked_from: string;
};

type SessionUsageLine = {  // store.py:556-568 的 type:"usage" 记录行
  type: "usage"; ts: string; model: string; provider: string;
  input_tokens: number; output_tokens: number; total_tokens: number;
  cache_hit_tokens: number; cache_miss_tokens: number; cost_usd: number; cost_cny: number;
};

type UsageStats = {   // 01 契约 GET /v1/usage/summary 的响应形状（= UsageStore.stats 原样，usage_store.py:215-246）
  days: number; turns: number; input_tokens: number; output_tokens: number; total_tokens: number;
  total_cost: { usd: number; cny: number };
  by_model: { model: string; turns: number; total_tokens: number;
              total_cost: { usd: number; cny: number } }[];
  by_source: { source: string; turns: number; total_tokens: number;
               total_cost: { usd: number; cny: number } }[];
};
```

**两条如实的数据口径**：

- `tool_call` 的 `input` 字段经 `_jsonable` 处理后是 **Python repr 字符串而非 JSON 对象**（`api.py:440-447` 对非标量值一律 `str(value)`；`agent.py:449` 产出的是 dict）。前端按等宽文本渲染，**不得** `JSON.parse`。同理 `_jsonable` 保留了事件里的 `type` 键，payload 内有冗余 `type`——前端以 SSE 帧的 `event:` 行为准。
- **SSE 帧不含 `created_at`**（01 契约的帧格式只有 `id:`/`event:`/`data:`；快照端点的 SQL 取了 `created_at` 但帧里没有）。时间线 MVP 不显示逐帧时间戳；若要显示，需 01 在 `data` 内补字段（挂账）。

### 浏览器本地状态

- `nexuscli.workbench.apiKey`：API key 明文存 localStorage。如实披露：key 会留在浏览器 profile 里，且 SSE 的 `api_key` 查询参数会进本机浏览器历史（01 已披露同款风险）；缓解是绑定 127.0.0.1 + 静态资源无用户数据（01「数据流·静态资源数据流」）。多用户/更体面的 token 方案是 01 挂账③，本文不重复设计。
- `nexuscli.workbench.threads`：本浏览器创建的线程列表 `[{id, title, createdAt, lastTurnAt}]`，title 取首条 prompt 去空白后前 80 字（对齐 `store.py:23` 的 `_TITLE_MAX_CHARS` 风格）。这是会话列表 MVP 的数据源之一，**不是**服务端枚举。

## 数据流

### SSE 消费统一策略（F4 回放与 F7 观察共用）

转录回放与 turn 观察走同一条流、同一个 hook `useEventStream(threadId, apiKey)`：

1. **打开**：`new EventSource('/v1/threads/{id}/events/stream?since=0&api_key=' + encodeURIComponent(key))`。`since=0` 从头全量回放（01「数据流·SSE 连接生命周期」：缺省 0 与快照「返回全量」语义一致），刷新页面后经此续看。
2. **帧处理**：每条 message 记 `lastId = Number(e.lastEventId)`，帧按 `id` 去重后 push 进 `frames[]`；`data` 单行 JSON parse。合并时以 id 去重是手工重连竞态的兜底。
3. **错误分类**：EventSource 不暴露 HTTP 状态码（浏览器 API 限制，如实写）。首次 onerror 时发一次快照请求 `GET /v1/threads/{id}/events`（带 `x-api-key` 头）分类：401 → 回 key 门提示 key 无效；404 → 线程不存在空态；其余 → 进入重连退避。
4. **手工重连**：按 01 的前端约定「出错即 close、携带最后收到的 `id:` 手工重开」，退避序列 1s/2s/4s/8s/10s 封顶，重开 URL 带 `since=lastId`——服务端按 `id > since` 重放，天然无洞（01 断线重连语义）。不依赖 EventSource 原生自动重连（它不带 `since`，会重放全量）。
5. **降级**：连续 5 次重连失败（含 503 流槽满，01 契约 `MAX_SSE_CLIENTS = 16`）→ 切快照轮询模式：每 2s `GET /v1/threads/{id}/events` 全量重绘，状态徽标显示「降级轮询」。快照无 id 列（`api.py:357-375` 只取 type/payload/created_at），故降级模式下不提供续看游标，恢复 SSE 需用户刷新。
6. **心跳**：`": ping"` 注释行被 EventSource 忽略（01 契约），无需处理；空闲连接靠它保持活性。

同一时刻 MVP 只开一条流（单转录视图），远低于 16 槽上限；审批是 REST 轮询不占流槽。

### 时间线归并规则（F4，Timeline.tsx 的确定性算法）

输入 `frames[]`（按 id 升序），输出 turn 分组的条目序列：

- `thread.created` → 头部分隔（线程 ID 展示）；`turn.started` → 开一个 turn 组，用户消息展示 `payload.message` + mode 徽标。
- 连续 `message.delta` → 按 id 序拼接进当前 turn 的助手正文缓冲（`payload.text` 逐帧追加），渲染为单条 pre-wrap 文本。
- `tool_call` → 开一张工具卡片（`payload.name`，`payload.input` 等宽文本）；紧随的 `tool_result` 按「最近一张未闭合的同名卡片」并入（`payload.is_error` 为 true 红色描边）；`error` 帧 → 红色错误条（`payload.error` 文本）。
- `turn.completed` / `done` → 关闭当前 turn 组，展示 `payload.total_tokens`。**error turn 没有这些帧**：`_run_turn` 在 error 帧后直接 raise（`api.py:188-189`），终态由 POST 的 500 响应（`api.py:156-159`）或「后续再无帧 + POST 失败」判定——时间线以 error 条收尾即可，不伪造完成态。

### F4 会话视图数据流

列表（服务端 `GET /v1/sessions` 枚举 + 本浏览器线程记忆 + 手动 ID 输入框）→ 选中线程或会话 → 路由用 hash 定位（`#/threads/<id>`，无路由库，刷新后直接落回该线程的回放）→ `useEventStream` 全量回放 → live。REPL 会话经 `GET /v1/sessions/{id}/transcript` 读取后走同一 Timeline 组件渲染。视图切换不关流（F7 观察复用），仅当离开转录视图超过一个 turn 周期后才允许关闭（简化：MVP 常驻不关，一条流成本可忽略）。

### F5 任务面板与 usage 视图数据流

- 任务面板：进入视图即拉 `GET /v1/tasks`，可见期间每 2s 轮询（与审批轮询共用节奏常量）；行内展示 id、mode、StatusPill、created_at/updated_at、attempts、scope；展开行显示 prompt、result/error、lease_expires_at、worker_id。取消按钮仅 queued/running 可见 → `POST /v1/tasks/{id}/cancel` → 以响应 `{"canceled": bool}` 提示（false = 任务已结束，`api.py:151-153`）→ 立即刷新列表。不做乐观更新，MVP 以轮询收敛为准。
- usage 视图：天数窗口切换（1/7/30）→ `fetchUsageSummary(days)`（`GET /v1/usage/summary`，01 契约）→ 汇总卡（turns / total_tokens / total_cost.usd / total_cost.cny）+ by_model 表 + by_source 表（纯 HTML 表格，无图表库）。无挂账：端点已由 01 定稿、响应 = `UsageStore.stats` 原样（`usage.db` 不存在时显示全零结构不报错）。

### F7 turn 前端状态机（Composer + TranscriptView 协作）

```
idle ──submit(prompt, mode)──▶ sending ──SSE 首帧/POST 已发出──▶ streaming ──┬─turn.completed/done 或 POST 200──▶ completed
                                                                             └─error 帧或 POST 4xx/5xx─────────▶ failed
completed/failed ──再次 submit──▶ sending（新 turn，同一 thread）
```

1. `submit`：无当前线程先 `POST /v1/threads` 取 `{"id"}`（`api.py:113-115`），写入 localStorage 线程记忆；输入空串客户端直接拒绝（服务端 400 口径，`api.py:122-124`）。mode 下拉限定 react/plan/team（`tasks.py:9` 的 `TASK_MODES`，服务端校验在 `api.py:461-465`）。
2. `sending`：`ensureStream(threadId)` 保证 SSE 已开（`since=0`），然后发出 `POST /v1/threads/{id}/turns`（**不 await 于 UI 主流程**——它要等整个 turn 结束才返回，见「业务逻辑·F7」第 1 条），随即进入 `streaming`。
3. `streaming`：帧驱动渲染（时间线归并规则同上）；`message.delta` 追加流式正文；工具调用卡片实时出现。若 `GET /v1/approvals` 出现 `scope_id == 当前线程` 的挂起审批（01 契约字段），composer 显示「等待审批」徽标——此间事件流静止是**预期行为**（审批挂起，01「数据流·审批数据流」），不是断线。
4. `completed/failed`：以「先到的终态信号」定稿——`turn.completed`/`done` 帧或 POST 响应（200 带 `total_tokens` / 非 200 带 `error`，`api.py:156-159,199-204`）；POST 结果对 SSE 丢帧兜底。LLM key 缺失时服务端 400（`api.py:284-288` 的报错文案），错误条原样展示服务端 message。
5. `streaming` 期间 composer 禁用（同线程串行锁，`api.py:126-129`）；`failed` 后允许直接重试（发起新 turn）。
6. 审批卡片由 App 外壳的 2s 轮询驱动（01 定的节奏与字段），决策 `POST /v1/approvals/{id}/decision`，只渲染 approve/deny 两个按钮、description 作 Reason 行、缺失时显示 `(no reason provided)`（01「数据流·审批数据流」第 3 步的 UI 口径）。

## 接口与边界

### 前端消费的端点清单（全部引用，不改名）

**01 契约新增端点**：

| 端点 | 前端用法 |
| --- | --- |
| GET `/v1/threads/{id}/events/stream?since=N[&api_key=...]` | 转录回放与 turn 观察（`useEventStream`）；浏览器 `EventSource` 无法设自定义头，故必须用 `api_key` 查询参数（01 鉴权扩展只对此路径生效）；401/403/404/503 语义见 01 契约表 |
| GET `/v1/approvals` | App 外壳 2s 轮询，渲染审批卡片（description 作 Reason 行） |
| POST `/v1/approvals/{id}/decision` | 审批决策 `{"decision":"approve"\|"deny"}`，其余动词 400、未知/已解决 id 404（01 契约） |
| GET `/`、GET 非 `/v1/*`（仅 `--workbench`） | dist 托管 + SPA 回退（无后缀路径回 `index.html`，01 契约），无鉴权 |
| GET `/v1/sessions`、GET `/v1/sessions/{id}/transcript` | 会话列表与转录回放（01「F4/F5 的服务端半边」契约，鉴权同标准门） |
| GET `/v1/usage/summary?days=N` | usage 统计（01 契约，响应 = `UsageStore.stats` 原样） |

**既有端点**（前端直接消费，行为不变）：`POST /v1/threads`（`api.py:113-115`）；`POST /v1/threads/{id}/turns`（`api.py:116-130`）；`GET /v1/threads/{id}/events` 快照（错误分类与降级轮询用，`api.py:131-136`）；`GET /v1/tasks`、`GET /v1/tasks/{id}`、`POST /v1/tasks/{id}/cancel`（`api.py:137-153`）。

### 契约缺口（复审后收口：服务端读端点已由 01 定稿）

（2026-10-25 复审修复：本节原记录的两个缺口——REPL 会话读取与 usage 汇总——已由 01「F4/F5 的服务端半边」节定稿端点并同步回本文件，F4/F5 不再有占位降级口径。以下保留仍然成立的如实说明与给服务端实现的现成答案。）

1. **REPL 会话转录的并发一致性（给 01 服务端实现的现成答案）**：磁盘格式定稿于 `store.py`（首行 meta `{"type":"meta", ...SessionMeta 字段}`，`store.py:579-584`；正文为消息行 `{"role","content","name","tool_call_id","tool_calls"}`，`store.py:192-199`，与 `type:"usage"` 记录行混排，`store.py:556-568`；损坏行跳过，`store.py:330-341`）。`SessionWriter` 每次追加都是临时文件 + `os.replace` 原子替换（`store.py:606-610`），且 `append` 路径是整文件重写（`store.py:509-524`），读者只会看到旧或新的完整内容——master 的开放问题「写入中的转录如何安全 tail」由此消解，服务端 `/v1/sessions/{id}/transcript` 直接整文件读即可，无需 tail。
2. **SSE 帧无时间戳**：见「数据模型」如实口径第 2 条（仍未定稿的唯一下沉挂账，前端时间线 MVP 不显示逐帧时间戳）。

### 边界（本轮明确不做）

- **MVP 无前端单元测试框架**（vitest 不进本轮，master 边界原文）：前端质量门槛 = `tsc -b && vite build` 构建通过 + Python 侧端点 smoke（见「验收钩子」）；前端测试基建挂到 F8 之后再议。
- 不引入路由库、状态库、UI 组件库、markdown 渲染库、图表库（理由见「业务逻辑·F3」）。
- 不做 WebSocket、多用户认证、公网部署（01 已定稿 SSE；绑定 127.0.0.1 不变）。
- 不改 REPL/TUI 既有交互；工作台是新增入口。
- dist 打进 wheel 维持 01 挂账①（当前依赖仓库检出或 `NEXUSCLI_WORKBENCH_DIST` 覆盖）；更体面的 SSE 鉴权维持 01 挂账③。
- 时间线不显示逐帧时间戳（帧内无该字段，挂账见上）。

### 任务派生提示（供 /spec:new 建包）

（原第 0 项「服务端契约补稿」已由 01「F4/F5 的服务端半边」节收口，端点归 01 的任务派生提示⑥，前端轮消费之。）

1. F3 工程脚手架：目录树 + package.json/tsconfig/vite.config + `.gitignore` 两处改动（根加 `!frontend/dist/`，frontend 加 `node_modules/`）+ 首次 `npm install` 生成并提交 package-lock.json + `npm ci && npm run build` + dist 入库提交 + tests/test_runtime_static.py 增「已提交 dist」用例（见验收钩子）。
2. F4 骨架：App 外壳（key 门 + tab + hash 定位）+ `useEventStream`（回放/重连/降级三路径各有手工验证记录）+ Timeline 归并 + SessionsView（服务端 `/v1/sessions` 列表 + 本地线程记忆 + 手动 ID + 转录回放）。
3. F5：TasksView（轮询/详情/取消）+ UsageView（`/v1/usage/summary` 消费，组件/类型/fetch 全量交付）。
4. F7：Composer 状态机 + 线程记忆写入 + 审批卡片接入（ApprovalCard + 2s 轮询）+ 端到端 smoke。

## 验收钩子

pytest 环境由 `tests/conftest.py:19-37` 的 autouse fixture 重定向 HOME 与 USERPROFILE，本文全部新测试不绕开（本特性的 Python 侧测试不触碰用户目录数据）。**如实声明边界**：F3-F5/F7 没有前端单元测试（vitest 不进本轮），Python 侧也没有为前端逻辑新增大块测试——后端端点语义的测试由 01 文档定稿（`tests/test_runtime_sse.py`、`tests/test_runtime_static.py`、`tests/test_runtime_approvals.py`），本文只新增「已提交 dist」一个用例；F4/F5/F7 的端到端证明以真实进程 smoke 为准。

**构建与入库（F3，命令级门槛）**：

```bash
cd frontend && npm ci && npm run build   # 期望 exit 0，产出 frontend/dist/index.html
git ls-files frontend/dist | grep -c .   # 期望 ≥ 1（构建产物已被 git 跟踪）
git check-ignore -v frontend/dist/index.html; echo "exit=$?"
# 期望 exit=1（无输出）——锁住根 .gitignore:9 的 dist/ 不会重新吞掉构建产物
node --version                            # 期望 >= 20.19（engines 门槛）
```

**Python 回归（命令：`uv run python -m pytest -q tests/test_runtime.py tests/test_runtime_sse.py tests/test_runtime_static.py tests/test_runtime_approvals.py`，期望全绿）**：

- `tests/test_runtime.py` 既有 9 个用例回归（任务生命周期/取消/原子领取/lease 回收/mode 校验/turn 历史持久化/路由 smoke，`tests/test_runtime.py:16-141`）。
- `tests/test_runtime_static.py` **新增一个用例**（放 01 已定稿的该文件里，不另开文件）：「已提交 dist 的真实托管」——`RuntimeApiServer(cwd=仓库根, ...)`（仓库根取 `Path(__file__).resolve().parents[1]`，不设 `NEXUSCLI_WORKBENCH_DIST`），`GET /` 断言响应体前 15 字符经 `lower()` 后等于 `<!doctype html>`（**大小写不敏感比较**：HTML5 doctype 关键字不分大小写，Vite react-ts 模板实测就是小写 `<!doctype html>`——本次 `npm create vite --template react-ts` 脚手架验证；写死大写会让照模板生成的入口文件假失败）且 `content-type: text/html; charset=utf-8`；`frontend/dist` 不存在时 `pytest.skip`（wheel 检出形态）。测试凭据沿用 `tests/test_runtime.py:11-13` 的 `_runtime_test_key` 占位惯例；真端口构造照 01 的 `_build_http_server` port=0 注入口径。

**lint**：`uv run ruff check . && uv run ruff format --check .`（本轮 Python 改动极少时照跑，保持仓库惯例）；前端类型检查即 `npm run build` 内的 `tsc -b`。

**smoke（真实进程，人工执行一次并入任务验证记录）**：

```bash
# 1) 构建产物托管
NEXUSCLI_RUNTIME_API_KEY=dev-key uv run nexuscli serve --workbench --port 8080 &
curl -sS http://127.0.0.1:8080/ | head -1         # 期望首行 lower() 后为 <!doctype html>（大小写不敏感；01 smoke 同口径；区别于 F2 交付时的空态页）
# 2) dev proxy
cd frontend && npm run dev &                        # Vite 默认 http://localhost:5173
curl -sS -H 'x-api-key: dev-key' http://localhost:5173/v1/tasks   # 期望经 proxy 返回 {"tasks":[...]} JSON
# 3) 端到端 turn 流（F7），curl 序列照 docs/runtime-api.md:14-47
#    前置条件：环境已配置 LLM key（llm.api_key 配置链；NEXUSCLI_RUNTIME_API_KEY 只是本 API 的门禁，不是 LLM key）。
#    _ensure_llm_key 先于 turn.started 执行（api.py:164→284-288），无 LLM key 时 POST 立即 400、事件流零新帧。
curl -sS -X POST http://127.0.0.1:8080/v1/threads -H 'x-api-key: dev-key'
curl -sS -X POST http://127.0.0.1:8080/v1/threads/<thread_id>/turns \
  -H 'content-type: application/json' -H 'x-api-key: dev-key' \
  -d '{"message":"用一句话介绍这个仓库","mode":"react"}' &     # POST 同步阻塞，放后台
curl -sS -N 'http://127.0.0.1:8080/v1/threads/<thread_id>/events/stream?since=0&api_key=dev-key'
# 期望：立即回放既有帧；message.delta 逐帧到达；结束见 turn.completed（total_tokens 字段）
# 无 LLM key 环境的替代断言（如实标注为错误路径验证，不能替代端到端证明）：
#   POST 返回 400，message 为 "NEXUSCLI_API_KEY is not configured. Runtime turns/tasks need a working LLM key."
#   （api.py:286-288）；SSE 只回放既有帧、无新帧。此形态下 F7 的端到端证明未完成，不得据此勾选。
```

**浏览器人工验证清单**（并入任务验证记录，每条对应一个业务断言）：

- 打开 `http://127.0.0.1:8080/` → key 门 → 提交 turn → 流式正文逐字出现、工具调用卡片成对闭合、结束后 token 计数出现（F7 状态机 completed 路径；前置：环境已配置 LLM key，同 smoke 第 3 步）。
- 刷新页面 → 同一 hash 落回该线程 → 时间线完整重现（since=0 回放）且能继续观察新 turn（F4 续看）。
- 提交需要审批的工具调用（如「把 X 写入文件」，需要 LLM key）→ 审批卡片带 Reason 行出现 → 批准后 turn 继续、拒绝后时间线出现拒绝结果（F6/F7 协同；超时 300s deny 不人工等，以 01 的 `tests/test_runtime_approvals.py` 覆盖为准）。
- 任务面板：`POST /v1/tasks` 提交后台任务后列表 2s 内出现，取消 queued 任务状态变 canceled（F5；数据源与 `curl /v1/tasks` 一致）。
- REPL 会话视图：`GET /v1/sessions` 列表按 updated_at 倒序出现、cwd 过滤生效（默认只见本项目，`all=1` 见全部）；点开会话 → `GET /v1/sessions/{id}/transcript` 转录按行渲染（F4；非法 id 404 由服务端测试覆盖）。
- usage 视图：`GET /v1/usage/summary` 切换 7 天窗口后汇总卡与 by_model/by_source 两表渲染出 `UsageStore.stats` 形状的数据（F5；`usage.db` 不存在时显示全零结构不报错）。
