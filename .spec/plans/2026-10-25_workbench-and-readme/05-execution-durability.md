# 05 — 执行可靠性：F9 工具副作用日志 / F10 回合级 checkpoint / F11 引擎结果合同 / F12 Anthropic provider 对齐 / F13 mypy 渐进接入

本文件定稿 master「假设与开放问题」中下沉到 05 的全部裁决：工具日志库的位置、schema 版本化与轮换策略，
三套持久化的真相分工，F11 结果合同的覆盖入口与改造半径，F12 现状盘点结论。语义规格对标 kitagent
已验证实现（clone 实测源码：`/tmp/kitagent-src/src/agent_runtime/core/graph/recovery.py`、
`graph/tool_node.py`、`tools/invocation.py`、`engine/base.py`、`llm/provider.py`、`graph/checkpoint.py`、
`loop.py`，本会话逐一读过），落点全部在 NexusCLI 既有结构上自研等价物，不引入 LangGraph、
不照搬多租户恢复外壳。与工作台线（01/02 定稿的 API 面）完全独立，不改动 `src/nexuscli/runtime/api.py`
的对外接口设计。

## 业务逻辑

### 依赖与实现顺序（任务拆分的 depends-on 依据）

F9 是 F10 的 fail-closed 底座，必须先做；F11 为 F6（Web 审批）与 F10（恢复语义）提供统一语言，
可先行或与 F9 并行；F12、F13 相对独立可并行。建议依赖图：

- F9（工具日志）：无前置。
- F11（结果合同）：类型定义与事件折叠无前置；唯一例外是 `budget_exhausted` 挂起的
  `checkpoint_id` 填充（warning 事件携带最近 checkpoint id）depends-on F10——F11 先行落地时该
  字段允许为 None，验收按两档写（见验收钩子）。
- F10（回合 checkpoint）：depends-on F9（`outcome_unknown` 不自动重放）+ F11（挂起/终态以合同类型表达）。
- F12（provider 对齐）、F13（mypy）：无前置，任意轮次并行。

### F9 工具调用日志与幂等重放

语义完全对标 kitagent 的 claim 协议（`recovery.py` 的 `_claim_tool_sync`，recovery.py:1045-1104；
`invocation.py` 的接入时序，invocation.py:236-264）：

1. **认领（claim）**：带副作用的工具执行前先查日志，四种结局——
   - 无记录 → 插入 `started` 行并放行执行（action=`execute`）；
   - 已有 `completed` → **reuse**：反序列化 `serialized_result` 直接回填为本次工具结果，
     handler 不再执行（kitagent：invocation.py:245-246）；
   - 已有 `started`（上次进程死在执行中途）→ **outcome_unknown，fail-closed**：不自动重放有
     副作用的调用，返回带稳定错误码 `outcome_unknown` 的 is_error 结果（kitagent 将
     `started`/`in_progress` 与 `outcome_unknown` 一律映射为 `ToolOutcomeUnknownError`，
     invocation.py:247-250；NexusCLI 侧按既有架构转为不中断回合的错误 ToolResult，见「接口与边界」）；
   - 同 `tool_use_id` 但 `tool_name` 或 `input_hash` 不同 → 类型化冲突错误
     `ToolJournalConflictError`（对标 `ToolInvocationConflictError`，recovery.py:1262-1272 的
     `_check_tool_identity`）。
2. **完成（complete）**：执行成功 → `serialized_result` 落库、状态转 `completed`；执行异常或超时 →
   状态转 `outcome_unknown`。日志写失败必须在副作用发生后原样上抛、不得触发重试（kitagent 的
   注释语义：invocation.py:353-357「journal or event-sink failures must propagate instead of
   dispatching the tool again」）。
3. **三态约束**：`started / completed / outcome_unknown` 由 SQLite CHECK 约束兜底；
   `completed` 必须有 `serialized_result`，非 `completed` 必须没有（DDL 见「数据模型」，
   对标 recovery.py:51-67）。
4. **接入范围（本轮定稿）**：只接入三类有副作用的内置工具——`bash`/`execute_command` 的**写类命令**
   （判定复用执行器既有的静态只读判定 `command_readonly`，executor.py:118-121；只读命令走既有
   readonly 直通，不进日志）、`write_file`（builtins.py:42-58）、`edit_file` 的**非 dry_run** 调用
   （builtins.py:59-89，`dry_run: true` 本身只读）。只读工具不进日志（噪声）；MCP 工具、`task`/
   `save_memory`/`save_skill`/`revert_turn` 等其余有副作用工具本轮不接入（挂账：接入方式相同，
   逐个补 `requires_journal` 声明即可，见「接口与边界」的声明式清单）。
5. **重放接入路径（两级语义，评审修正后定稿）**：`tool_use_id` 是模型每次调用新生成的 nonce，
   「让 LLM 重新生成调用」的恢复路径拿不到原 id——这是 kitagent 原型成立的前提差异：kitagent
   恢复时重放的是持久化的在途调用对象（graph state 的 `pending_tool_calls`，state.py:27-28），
   不是重新生成。因此 NexusCLI 分两级，各自如实声明保证强度：
   - **精确重放（id 级，强保证）**：适用于「在途调用按原 id 重放」的路径——F10 的 `/turn-resume`
     （回合 checkpoint 在执行前快照点持久化 pending 调用的原始 id，见 F10 写入时机）。保证：
     同 `(scope, run_id, tool_use_id)` handler 至多执行一次；`completed` 复用、`started` →
     `outcome_unknown` fail-closed。
   - **尽力去重（hash 级，弱保证）**：适用于「经全新 LLM 对话重跑任务」的路径——`/plan --resume`
     重跑被归一化回 PENDING 的任务（plan/models.py:282-288 的 RUNNING→PENDING 归一化，
     plan_execute.py:131-174 `run_resume` → plan_execute.py:323 经 `query(history=[])` 全新对话）、
     team react worker（orchestrator.py:174-185 经 `query()`）与后台 lease 恢复（tasks.py:183-200
     `requeue_expired` 后 worker 重跑）。机制：journal 增二级索引
     `(scope, run_id, tool_name, input_hash)`；**沿用原 run_id 的恢复 run** 内新生成的写类调用先按
     `(tool_name, input_hash)` 查同 run_id 的既有记录——命中 `completed` → 复用；命中
     `started`/`outcome_unknown` → fail-closed；未命中 → 正常执行。**如实声明**：hash 级是尽力
     去重不是 exactly-once——LLM 重生成的参数若有漂移（不同临时文件名、不同 diff 上下文）即
     miss 并照常执行；它防的是「中断前已完成同一写入、恢复后盲目重放」这条最常见路径。
   - run_id 延续是两级机制的前提：plan checkpoint 载荷增 `run_id` 字段（ExecutionPlan.to_dict
     顺延，plan/models.py:227）；`TaskRecord` 增 `run_id` 列（tasks.py:276-286 既有 alter-table
     迁移惯例）；回合 checkpoint 载荷增 `run_id` 字段（见数据模型）。
6. **诚实边界（写进文档正文）**：该日志是**面向确定性本地工具的重放协议**（写文件、确定性 shell
   写命令）。强保证只有一条：「同 `(scope, run_id, tool_use_id)` 至多执行一次 handler」，且只在
   按 persisted pending 调用原 id 重放的路径（F10 /turn-resume）上成立；hash 级去重是尽力而为
   （见第 5 条），不对任意 shell 命令、MCP 工具、外部 HTTP 服务承诺 exactly-once：外部副作用
   （网络写、远端状态变更）发生在 `started` 与 `completed` 之间的窗口内时，日志只能把状态如实标为
   `outcome_unknown` 并拒绝自动重放，不能撤销已发生的外部效果。**run_id 延续规则**：全新 run 生成
   新 `run_id`；**从 checkpoint / plan checkpoint / 任务租约恢复的 run 必须沿用被中断 run 的
   run_id**（注入接缝见「接口与边界」修改面清单）——否则两级去重都无法命中，这是实现时的硬约束。

### F10 ReAct 回合级 checkpoint 与断点续跑

- **写入时机（每轮两个快照点，评审修正后定稿）**：只在轮末写一份会让「assistant tool_use 已入
  messages（agent.py:400）但工具结果未落（agent.py:447-482 执行中）」这个最危险的崩溃窗口落在
  两次 checkpoint 之间，pending_tool_calls 也将恒为空。因此每轮写两次、同一文件覆盖：
  - **(a) 执行前快照（in-flight）**：assistant 消息已追加进 `messages`（agent.py:400）、工具分发
    （agent.py:447-455）尚未开始——此刻 `pending_tool_calls` = 本轮 `_finalize_tool_calls` 的产物
    （未执行）。执行中途崩溃，最后一份 checkpoint 恰好含这批带原始 id 的在途调用。
  - **(b) 轮末快照（round-end）**：工具结果已全部追加进 `messages`（agent.py:465-493）、决定继续
    下一轮之前——`pending_tool_calls` 为空，表示该轮完整。
  两份内容相同（对话消息、pending 调用、run 级 usage/cost、turn 序号、model/provider、cwd、
  run_id），(b) 覆盖 (a)。plan 模式不重复建：其每任务 checkpoint 已有（plan_execute.py:287）。
- **恢复入口（新增 REPL 命令 `/turn-resume [checkpoint_id]`）**：无参数列出当前 cwd 的候选
  checkpoint；带 id 接续——从 checkpoint 重建 `Agent.history`、**沿用 checkpoint 记录的
  `run_id`**（journal 去重命中的前提）、以 `usage_so_far`/`cost_so_far` 作为续跑的 usage 初值；
  然后按 pending 分流：
  - `pending_tool_calls` 非空（in-flight 崩溃）→ 逐个按**原 id** 经执行器+journal 重派：
    无记录 → 执行；`completed` → 复用结果补入 messages（该调用中断前已完成）；`started` →
    `outcome_unknown` 不自动重放（is_error 结果注明原因）；补齐后从本轮继续循环。
  - `pending_tool_calls` 为空（轮末快照）→ 直接从 turn+1 续跑。
- **三套持久化的真相分工表（定稿 master 开放问题）**：

  | 持久化 | 位置 | 是什么真相 | 什么场景权威 |
  |---|---|---|---|
  | 会话 JSONL | `~/.nexuscli/sessions/<id>.jsonl`（store.py:187-189） | 对话转录 + 每轮 usage 台账（store.py:539-572） | 「对话内容是什么、历史上花了多少 token」——`/resume`、`-c`、fork、检索都以它为准 |
  | 计划 checkpoint | `~/.nexuscli/plan_runs/<id>.json`（checkpoint.py:32-34） | 计划 DAG 的任务状态与结果（plan_execute.py:287 每任务后落盘） | 「plan/team 的计划推进到哪一步」——`/plan --resume` 以它为准 |
  | 回合 checkpoint（新） | `~/.nexuscli/turn_checkpoints/<checkpoint_id>.json` | 轮级执行状态：消息、待处理工具调用、usage 基线 | 「一个被中断的 react 回合从哪一轮、带着什么在途调用继续」——`/turn-resume` 以它为准 |

  冲突规则：`/turn-resume` 恢复时以回合 checkpoint 为权威快照重建内存历史，随后按既有
  `writer_for` 语义把消息落回会话 JSONL（store.py:261-265）；checkpoint 缺失或损坏时回落到
  既有会话恢复路径，不新增错误面。会话 JSONL 永远不被回合 checkpoint 改写既有行。
  「JSONL 比 checkpoint 新」的分叉场景：**当前接线下不会发生**——转录只在整回合完成后由
  `_persist_history` 追加（session_cmds.py:21-29，调用点 repl.py:336、362、742），被中断回合的
  部分消息从未落盘，checkpoint 恒领先或等于 JSONL。但裁决留档：未来若改为轮级追加导致 JSONL
  领先，`/turn-resume` 一律走 **fork**（新 session id、`forked_from` 标注，store.py:267-295 既有
  机制）而非截断——原转录保持「实际发生过什么」的完整事实，重放续跑写进分叉副本。
- **诚实边界（沿用既有挂账，不新增承诺）**：REPL 的 Ctrl+C 仍是中止而非协作式中断（master 边界
  不变；本轮不实现信号级协作中断）——checkpoint 的价值在于中止后**可从最近完整轮**接续，而不是
  中断那一刻的精确快照。checkpoint 明文落盘，沿用 `plan_runs` 现状口径（checkpoint.py:64-66 同为
  明文 JSON + temp/rename 原子写）：其中可能含工具结果里的文件内容，不做加密，文档如实说明。

### F11 引擎结果合同统一

- **形态**：`EngineResult = RunOutcome | RunSuspension` 的 sum type，对标 kitagent
  engine/base.py:47-100（`RunOutcome` / `RunSuspension` / `EngineRunResult`）。终态 `RunOutcome`
  携带 `status/result/reason/steps` 与类型化错误明细 `EngineErrorDetail(code, message)`；
  挂起 `RunSuspension` 携带 `reason` 与恢复引用（checkpoint id）。挂起是一等返回值，不是异常、
  不是 `None`。
- **SuspensionReason 三态（定稿）**：`approval_pending`（审批等待）、`collaborative_interrupt`
  （协作式中断）、`budget_exhausted`（预算耗尽）。本轮**真实生产者只有后两者**：
  `collaborative_interrupt` 对应 plan 的协作中断（plan_execute.py:59-61 的
  `request_interrupt` 与 plan_execute.py:186-197 的中断落盘分支）、team 的对应聚合点；
  `budget_exhausted` 对应 `max_turns` 用尽告警与 loop guard 中止（agent.py:410-411、502-516）。
  `approval_pending` 本轮**只定义类型、不接生产者，且是挂账而非条件任务**（复审修正，推翻原
  「双向接线」方案）：01 定稿的 F6 审批桥是**同步阻塞回调**——回调内部
  `event.wait(timeout=300)` 阻塞当前 turn/task 执行线程（01「数据流·审批数据流」第 2 步），
  引擎调用在审批期间根本不返回，**不存在可映射 `RunSuspension(approval_pending)` 的挂起出口**；
  把阻塞审批改造成挂起返回模型会推翻 01/02 已定稿的「审批挂起期间事件流静止 + POST 同步
  阻塞」语义，收益不成比例。未来若审批模型异步化（SSE 推送审批、任务级挂起恢复），需先修订
  01 的审批桥设计，再回来接 `approval_pending` 生产者——在那之前该三态值只出现在类型定义
  与文档里，不出现在任何运行路径。
- **类型化错误码（首批）与分类机制**：码表 `llm_error`（流错误，agent.py:394-396）、
  `plan_no_checkpoint`（plan_execute.py:141-146）、`tool_invocation_conflict`、`outcome_unknown`、
  `agent_failed`（其他编排层异常）。error 事件携带的是裸 Exception（agent.py:394-396），三类来源
  都是 RuntimeError/ValueError，按异常类型无法区分——因此分类靠**发射点标注 + 类型化异常**，
  不做消息字符串匹配：
  - 发射点增可选键 `error_code`（error 事件的 dict 加键，向后兼容）：`agent.py:394-396` 处
    固定 `"llm_error"`（该处只会是流错误）；`plan_execute.py:112-114` 处经 `classify_error(exc)`。
  - 本轮引入的最小类型化异常集（均在修改半径内）：`PlanCheckpointMissing(ValueError)` 取代
    plan_execute.py:144-145 的裸 ValueError；journal 异常族（`tools/journal.py`）自带稳定码。
  - `classify_error(exc)`（results.py）：isinstance 命中上述类型 → 对应码；其余 → `agent_failed`。
    llm/base.py 的 `is_transient_api_error`/`is_context_overflow_error`（base.py:16-45）维持既有
    用途（planner/orchestrator 重试分类、overflow 恢复），不进入码表分类。
  码表集中在合同模块，新增码只增不改语义。
- **改造半径（收窄，写死）**：只动 agent 编排层的**返回合同与分发点**——`agent.py` 的模式分发
  （agent.py:199-207）下游三个 runner 的终态聚合、`plan_execute.py`/`orchestrator.py` 的终态
  分支（plan_execute.py:255-269）、`query_engine.py` 的事件折叠层（query_engine.py:98-120）。
  **不重写**工具层（executor/builtins）与 LLM 客户端（llm/）；**不改**事件流协议本身（REPL 渲染
  依赖的 `text_delta/tool_result/done/error` 事件一字不动）。
- **覆盖入口清单（定稿 master 开放问题）**：
  - 进本轮：SDK 面 `QueryEngine`（query_engine.py:76-96 的 `*_complete_*` 家族新增返回合同的
    `*_result_async` 变体，既有 raise 语义原样保留为薄包装）；`cli.py -p` 路径（经 QueryEngine，
    自动继承）；plan/team 编排层内部终态聚合点（typed outcome 取代裸字符串拼接）。
  - 本轮不动：REPL 渲染（事件流即界面，无返回值可换）；Runtime API（`runtime/api.py` 的
    `_run_task`/turns 属工作台线 01/02 已定稿面，F11 不触碰该文件）。
  - 挂账：`nexuscli` 顶层 `--resume-turn` flag（REPL `/turn-resume` 先行，顶层 CLI 面等有真实
    需求再扩）。`approval_pending` 生产者同为挂账（不是条件任务）：F6 审批桥是同步阻塞回调、
    无挂起出口（见「业务逻辑」F11 三态定稿），异步化审批模型之前不接线。

### F12 Anthropic provider 细节对齐（现状盘点 → 补齐清单）

现状盘点结论（本会话通读 src/nexuscli/llm/anthropic.py 全文后的定稿）：

| 子项 | 现状 | 结论 |
|---|---|---|
| max_tokens 截断落 tool_use 中间 | anthropic.py 已正确映射 `stop_reason=max_tokens`（anthropic.py:318-325），但 react 循环只按「无工具调用才收尾」判断（agent.py:404），`max_tokens` + 截断 tool_calls 会照常进执行；截断 JSON 经 `_tool_input` 退化为 `{"raw": ...}`（agent.py:628-636）被执行 | **缺**：补「stop_reason=max_tokens 且有 tool_calls → 全部合成 is_error tool_result、不执行」，保住 assistant tool_use 必有配对 tool_result 的不变量（对标 kitagent tool_node.py:104-107 与 loop.py:102-111 的合成口径） |
| thinking 块带 signature 原样回传 | 流式侧有 `thinking_delta` 透传（anthropic.py:263-264），但 `_format_messages` 组装 assistant 消息只有 text+tool_use 块（anthropic.py:208-223），thinking/signature 在历史中丢失；`Message` 类型无承载字段（types.py:11-16） | **缺**：`Message` 增可选 `thinking_blocks` 字段，anthropic 客户端在 assistant 内容块头部原样回传 `{"type":"thinking","thinking":...,"signature":...}`（对标 provider.py:152-161），会话序列化（store.py:192-210）同步携带 |
| 传输层流断线重试 | anthropic 路径单次尝试：`RemoteProtocolError/ReadError/ConnectError` 全部落入 `httpx.RequestError` 兜底分支直接报错（anthropic.py:151-158），无重试 | **缺**：补 4 次尝试、退避 1/2/4s 的重试，只重试这三类传输错误；语义错误（HTTP 状态错误、超时）不重试；重试期间不重复发布 token 事件（机制与参数口径见「数据流」） |
| prompt caching 打位 | `system` 是纯字符串（anthropic.py:174）、tools 无 cache_control（anthropic.py:177-188）；`cache_read_input_tokens` 已映射（anthropic.py:244、292） | **缺打位**：system 改为带 `cache_control: ephemeral` 的块数组、最后一个工具追加 `cache_control: ephemeral`（对标 provider.py:72-84）；usage 增映射 `cache_creation_input_tokens → cache_miss_tokens`（types.py:20-26 字段已够用） |

### F13 mypy 渐进接入 CI（摸底结果与清单生成方式）

- **摸底（2026-10-25 实测）**：mypy 当前不在 dev extras（`uv run mypy` 报 `Failed to spawn: mypy`），
  用临时环境执行 `uv run --with mypy mypy src/nexuscli`，结果：**Found 100 errors in 30 files
  (checked 122 source files)**。按文件分布（前几名）：skill/registry.py 16、tools/commands.py 14、
  tools/builtins.py 12、tools/background.py 11、tools/file_ops.py 8、entrypoints/repl.py 7、
  entrypoints/commands/skill.py 7、context/manager.py 7；同时 `llm/` 全部 6 文件、`plan/` 全部
  3 文件、`runtime/tasks.py`、`agent/plan_execute.py`、`agent/orchestrator.py`、`agent/query*.py`
  均为 0 错误。
- **排除清单生成方式（定稿）**：以摸底的 30 文件清单为准，生成**逐模块**的
  `[[tool.mypy.overrides]]` `ignore_errors = true` 排除项写入 pyproject.toml（一次性脚本生成、
  以字面清单提交，不用通配整个子包，保证粒度可收敛）。基线命令与日期写进清单注释。
- **纪律**：排除清单**只减不增**——新增模块与本轮改动模块（F9 journal、F10 checkpoint、
  F11 results、F12 anthropic.py 改动）必须在清单外通过；CI 的 mypy job 即机械保证（清单外
  任何文件出现新错误都会红）。
- **诚实边界**：不承诺本轮全仓清零；最终目标全仓清零（对标 kitagent core 清零）写为目标态，
  清零节奏由后续轮次按「每轮顺手修掉已触碰文件」推进。

## 数据模型

### F9 工具日志（新文件 `src/nexuscli/tools/journal.py`）

- **库位置（定稿 master 开放问题）**：`~/.nexuscli/tool_journal/journal.db`——沿用 `~/.nexuscli`
  单根惯例（sessions/、plan_runs/、usage.db、bg-subagents/ 并列）；路径经 `Path.home()` 解析，
  自动被 tests/conftest.py:19-37 的 autouse fixture 重定向。
- **schema 版本化（定稿）**：元数据表 `journal_schema(id INTEGER PRIMARY KEY CHECK (id = 1),
  version INTEGER NOT NULL)`，首版 version=1；初始化时发现既有库无元数据表但有业务表 →
  `ToolJournalSchemaError` 拒绝操作；版本不匹配 → 同样拒绝（对标 recovery.py:26-31、242-249）。
  升级策略：version 只增，跨版本迁移函数逐版编写，暂无 v2。
- **轮换策略（定稿）**：首版**不做自动轮换**。理由：单用户 CLI 体量有界（每行只存输入哈希与
  序列化结果，不存原始参数）；日志按 `(scope, run_id)` 自然分区。提供手动清理 SQL 路径
  （`DELETE FROM tool_invocations WHERE updated_at < ...`）写入文档；自动 prune 命令挂账
  （待出现首个真实体积问题再做）。
- **业务表 DDL（对标 recovery.py:51-67，裁剪多租户列）**：

```sql
CREATE TABLE tool_invocations (
    scope             TEXT NOT NULL,   -- 解析后的工作区绝对路径（对齐 tasks.py:32-34 的 scope 口径）
    run_id            TEXT NOT NULL,   -- 每次 Agent.run() 的 uuid4 hex
    tool_use_id       TEXT NOT NULL,   -- 模型侧调用 id（call["id"]）
    tool_name         TEXT NOT NULL,
    input_hash        TEXT NOT NULL,   -- 规范化 JSON 的 sha256，不存原始参数
    status            TEXT NOT NULL CHECK (status IN ('started','completed','outcome_unknown')),
    serialized_result TEXT,            -- {"content","is_error"} 的规范化 JSON
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (scope, run_id, tool_use_id),
    CHECK ((status = 'completed' AND serialized_result IS NOT NULL)
        OR (status != 'completed' AND serialized_result IS NULL))
);
CREATE INDEX idx_tool_invocations_scope_run ON tool_invocations(scope, run_id);
CREATE INDEX idx_tool_invocations_hash
    ON tool_invocations(scope, run_id, tool_name, input_hash);
```

- **序列化结果格式**：`{"content": str, "is_error": bool}` 的规范化 JSON（`sort_keys` +
  紧凑分隔符 + `ensure_ascii=False`，对标 invocation.py:44-54；NexusCLI 的 `ToolResult` 还有
  `display_summary`，属展示层不落库）。反序列化做字段校验，坏 JSON/缺字段 →
  `ToolJournalConflictError`（对标 invocation.py:57-75）。
- **输入哈希**：`json.dumps(input, sort_keys=True, separators=(",",":"), ensure_ascii=False)`
  后 sha256 hex（对标 recovery.py:153-166）。原始参数不落库（master：只存哈希）。

### F10 回合 checkpoint（新文件 `src/nexuscli/session/turn_checkpoint.py`）

- 路径：`~/.nexuscli/turn_checkpoints/<checkpoint_id>.json`；`checkpoint_id = <session_id>-t<turn>`
  之外再加短随机后缀（对齐 store.py:231-233 的时间戳+uuid 惯例），避免同会话重跑覆盖。
- 载荷（JSON 对象；`run_id` 与 usage 字段为评审修正后新增）：

```json
{
  "type": "turn_checkpoint",
  "checkpoint_id": "...",
  "session_id": "...",
  "run_id": "…journal 去重键的一半, /turn-resume 恢复时必须沿用…",
  "cwd": "...",
  "model": "...",
  "provider": "...",
  "turn": 7,
  "messages": [ { "role": "...", "content": "...", "tool_calls": [] } ],
  "pending_tool_calls": [ { "id": "...", "function": {"name": "...", "arguments": "..."} } ],
  "usage_so_far": { "input_tokens": 0, "output_tokens": 0, "cache_hit_tokens": 0,
                    "cache_miss_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0 },
  "cost_so_far": {},
  "saved_at": 1760000000.0
}
```

  usage 字段口径说明（评审修正）：`usage_so_far`/`cost_so_far` 是 **agent 运行层**的累计值
  （`_run_react` 的 `total_usage`/`last_cost`，agent.py:285-287、498-500；Usage 形状见
  types.py:20-26），不是 REPL 会话层的 `SessionUsageTotals`——后者含 turns/成本汇总
  （store.py:88-120），在 Agent 内取不到。恢复时经构造接缝注入为续跑 run 的初始
  `total_usage`，使续跑 done 事件的 usage 覆盖整个逻辑回合；REPL 台账不受影响——
  `append_usage` 仍每完成回合记一次（session_cmds.py:73-94），被中断回合从未落账，
  重跑只记一次，无双计。

- 写入纪律：temp 文件 + `os.replace` 原子替换（同 store.py:606-610 与 checkpoint.py:64-66 的
  既有模式）；`load` 对缺失/损坏/字段非法一律返回 None 降级，绝不 raise（同 checkpoint.py:69-92
  的纪律）；保留每个会话最近 N=5 份，写入时顺带清理更旧的（防目录膨胀，属写入路径内聚逻辑，
  不是后台轮换）。
- `Message` 扩展（F12 共用）：`Message` 增可选字段 `thinking_blocks: list[dict[str, Any]] =
  field(default_factory=list)`（types.py:11-16）；会话序列化 `_message_to_dict/_message_from_dict`
  （store.py:192-210）与回合 checkpoint 的 messages 编解码同步携带该字段。

### F11 结果合同（新文件 `src/nexuscli/agent/results.py`）

```python
@dataclass(frozen=True)
class EngineErrorDetail:
    code: str      # llm_error / plan_no_checkpoint / tool_invocation_conflict / outcome_unknown / agent_failed
    message: str

@dataclass(frozen=True)
class RunOutcome:
    status: Literal["success", "failed"]
    result: str
    reason: str | None
    steps: int
    usage: Usage
    cost: dict[str, Any]
    error: EngineErrorDetail | None   # 仅 failed 可带

SuspensionReason = Literal["approval_pending", "collaborative_interrupt", "budget_exhausted"]

@dataclass(frozen=True)
class RunSuspension:
    reason: SuspensionReason
    detail: str
    checkpoint_id: str | None        # budget_exhausted 时指向回合 checkpoint

EngineResult = RunOutcome | RunSuspension
```

（形态对标 engine/base.py:47-100；`usage/cost` 来自 NexusCLI 既有 done 事件载荷
agent.py:518-529，不另造口径。）

### F13 mypy 配置（pyproject.toml 增量）

```toml
[project.optional-dependencies]
dev = [ "mypy>=1.11", /* 其余既有项不动, pyproject.toml:29-35 */ ]

[tool.mypy]
python_version = "3.11"
files = ["src/nexuscli"]
# 首版无全局严格开关: 与摸底基线(默认设置, 100 错/30 文件)同口径, 只收清单不加严格度

[[tool.mypy.overrides]]
module = ["nexuscli.skill.registry", "nexuscli.tools.commands", "nexuscli.tools.builtins",
          "nexuscli.tools.background", "nexuscli.tools.file_ops", "nexuscli.entrypoints.repl",
          # …以摸底 30 文件字面清单为准, 生成时补全; 清单只减不增
         ]
ignore_errors = true
```

CI（.github/workflows/ci.yml:8-38 之后新增独立 job，不动既有 test job）：
`mypy` job 复用 `astral-sh/setup-uv@v5` + `uv sync --extra dev --frozen` + `uv run mypy src/nexuscli`。
**锁文件纪律（评审补充）**：pyproject 增 mypy 依赖后必须 `uv lock` 再生 `uv.lock` 一并提交——
否则 CI 的 `--frozen`（ci.yml:29）会因 lockfile 与 pyproject 不一致直接报错，mypy job 会因错误
的原因变红。

## 数据流

### F9 正常执行流（落点：`ToolExecutor._execute_single`，executor.py:61-335）

```
审批门序(不动): 未知工具 → plan 只读门 → deny 规则 → hooks → 审批决策 (executor.py:99-245)
    ↓ 通过
journal 判定: _journal_eligible(tool, data)?
    ├─ 否(只读工具/只读 bash/edit_file dry_run) → 直接 tool.execute (executor.py:268-277, 原样)
    └─ 是 → journal.claim_tool(scope, run_id, tool_use_id, name, input_hash)
         ├─ execute      → tool.execute → 成功: complete_tool(序列化结果) → 返回结果
         │                                 → 异常/超时: mark_outcome_unknown → 既有错误路径
         ├─ reuse        → 反序列化直接返回(事件流照常发 tool_result, 不执行 handler)
         ├─ outcome_unknown → 返回 is_error ToolResult, 内容带稳定码 "[outcome_unknown] …"
         └─ conflict     → ToolJournalConflictError → executor 既有异常兜底
                          (executor.py:321-335) 转为带 "[tool_invocation_conflict]" 码的 is_error 结果
```

门序不变是硬约束：claim 发生在审批通过之后，日志不改变 deny/ask/allow 的任何判定。**reuse 的
审计语义（评审修正）**：reuse 分支不执行 handler，但必须留痕且与真实执行可辨——审计记录
（executor.py:278-287 的 `audit.record(outcome=…, approver=…)`）照发，`outcome` 按复用结果取
`allow`/`error`，`approver` 记新值 `journal-reuse`（`AuditLog.record` 的 outcome/approver 是
自由字符串，audit_log.py:29-38，新值安全，与既有 readonly-rule/run-scope/hitl 归因体系并列）；
遥测 span 的 `record_outcome` 记 `"reused"`。审计要能回答「这次结果是复用的还是新执行的」。

**注入面（评审修正，三处接缝，executor 不是注入点而是唯一执行点）**：`ToolContext` 增两个可选
字段 `tool_journal: ToolJournal | None` 与 `journal_run_id: str`（base.py:23-41）。构造接缝恰好
有三处，缺一则该路径写调用根本不入日志：
1. `Agent._run_react`（agent.py:252-258 建 context）——REPL 普通 turn；
2. `query()`（query.py:41-46 **自建** ToolContext，此前不在修改面清单，评审发现）——plan 任务
   （plan_execute.py:323-334 经 `query()`）与 team react worker（orchestrator.py:174-185）全部
   走这里，`query()` 增可选参数 `tool_journal`/`journal_run_id` 透传；
3. Runtime API worker 的 `_run_task`（api.py:260-282 经 QueryEngine → Agent）——注入
   TaskRecord.run_id。
不注入时行为与现状完全一致（向后兼容，测试可自由关闭）。

### F9 恢复流（两级语义，与「业务逻辑 F9 第 5 条」一一对应）

- `/plan --resume <id>`（**hash 级**）：`run_resume` 载入计划 checkpoint（plan_execute.py:131-174，
  checkpoint 载荷含原 `run_id`）→ 剩余/归一化回 PENDING 的任务经 `query()` 全新对话重跑
  （plan_execute.py:323-334，`query()` 带入沿用 run_id 的 journal 注入）→ 重生成的写类调用按
  `(tool_name, input_hash)` 查同 run_id 既有记录：`completed` 复用、`started/outcome_unknown`
  fail-closed、未命中照常执行（尽力去重，参数漂移会 miss）。
- team react worker（**hash 级**）：`_execute_worker` 经 `query()`（orchestrator.py:174-185），
  同上注入；team 的 plan 模式 worker 经 `PlanExecuteAgent`（orchestrator.py:150、224-230），
  与 `/plan --resume` 同路径——两条 worker 路径都要接注入，缺一不可。
- 后台任务 lease 恢复（**hash 级**）：`requeue_expired`（tasks.py:183-200、api.py:94、222）重派 →
  新 worker 以 TaskRecord 记录的原 `run_id` 重跑（`run_id` 列走 tasks.py:276-286 迁移惯例）→
  同 hash 级判定。
- `/turn-resume`（**id 级，唯一强保证路径**）：见 F10 恢复流——按 checkpoint 持久化的原始
  tool_use_id 重派，journal 精确命中。

### F10 写入/恢复流

```
写入(每轮两次, 同一文件覆盖; 失败仅告警不中断回合, 同 plan_execute.py:176-179 的纪律):
  (a) 执行前快照: assistant 已入 messages (agent.py:400)、工具分发 (agent.py:447-455) 之前
      → pending_tool_calls = 本轮 tool_calls(未执行); run_id 沿用当前 run
  (b) 轮末快照: 工具结果入 messages (agent.py:465-493) 之后
      → pending_tool_calls = []; 覆盖 (a)
恢复: /turn-resume <id>
  → load (损坏→提示无有效 checkpoint, 不 raise)
  → Agent.history ← checkpoint.messages; run_id ← checkpoint.run_id(沿用, 不新生成);
    total_usage/cost 初值 ← checkpoint.usage_so_far/cost_so_far
  → pending 非空(in-flight 崩溃): 逐个按原 id 经执行器+journal
      无记录→执行 / completed→复用结果补入 messages / started→outcome_unknown is_error 不重放
    pending 为空(轮末快照): 直接续跑
  → 继续 _run_react 循环
```

### F11 折叠流

```
事件流仅增可选键(error_code / warning.checkpoint_id), 既有键与序列不变
  → collect_engine_result(events) 折叠: done→RunOutcome;
    error 事件→优先读事件自带 error_code(发射点标注), 缺失时 classify_error(异常对象)兜底;
    max_turns 告警→RunSuspension(budget_exhausted), checkpoint_id ← warning 事件可选键
    (由 _run_react hit_limit 分支附带最近 checkpoint id; F10 未落地时无此键→None);
    plan 协作中断→RunSuspension(collaborative_interrupt)
  → QueryEngine 新增 *_result_async 返回 EngineResult; 既有 *_complete_* 包装保持 raise 语义(query_engine.py:107-108 不变)
```

### F12 重试时序（anthropic.py chat 内）

```
attempt 1: stream 开始, 逐事件即时 yield(现状行为)
  ├─ 传输断线(RemoteProtocolError/ReadError/ConnectError) → sleep 1s → attempt 2
  ├─ HTTPStatusError / TimeoutException → 不重试, 走既有错误事件(anthropic.py:136-150)
attempt 2/3/4: 全程静默收集(不 yield token/thinking delta——重试期间不重复发布), message_end/usage 只在
  最终成功那次之后发一次; 间隔依次 1s/2s/4s; 4 次皆断线 → 既有 RequestError 错误事件
```

参数口径（评审修正后统一）：**4 次尝试、3 次退避 1/2/4s**——这是对 kitagent 参数
（provider.py:22-23 的 `_MAX_STREAM_RETRIES = 3`，其 4s 档实际不可达）的有意修正，以兑现
master「按 1/2/4s 退避」的字面口径：3 次尝试只有 2 次退避，4s 永远用不上。

与 openai_compatible 既有「已产出 delta 就不重放」语义（openai_compatible.py:262-267、430-436）的
关系：Anthropic 路径按 ask 要求实现**静默重放**——已发布的 attempt-1 前缀 delta 与最终消息之间
可能出现显示层跳变（终端里前缀后直接接完整消息），此为有意取舍、写进 F12 落地时的模块
docstring；OpenAI-compatible 路径本轮不动。

## 接口与边界

### 新增/修改面清单

| 文件 | 动作 | 公开面 |
|---|---|---|
| `src/nexuscli/tools/journal.py` | 新增 | `ToolJournal(path)`、`hash_tool_input()`、`ToolClaim(action, record)`、`claim_tool()/complete_tool()/mark_outcome_unknown()`、异常族 `ToolJournalError/ToolJournalSchemaError/ToolJournalConflictError/ToolJournalNotFoundError` |
| `src/nexuscli/session/turn_checkpoint.py` | 新增 | `TurnCheckpointStore(root=None)`、`save()/load()/latest(cwd)`、`turn_checkpoints_root()` |
| `src/nexuscli/agent/results.py` | 新增 | `RunOutcome/RunSuspension/EngineErrorDetail/EngineResult`、`collect_engine_result()` |
| `src/nexuscli/tools/base.py` | 修改 | `ToolContext` 增 `tool_journal`/`journal_run_id` 可选字段；`Tool` 增 `requires_journal: bool = False` 声明位（本轮只有 bash 写类/write_file/edit_file 置真，后续接 MCP 工具时逐个补） |
| `src/nexuscli/tools/executor.py` | 修改 | `_execute_single` 在审批通过后插入 journal 分支（门序不动） |
| `src/nexuscli/types.py` | 修改 | `Message.thinking_blocks` 可选字段 |
| `src/nexuscli/llm/anthropic.py` | 修改 | retry 包装、cache_control 打位、thinking 回传（不新增类，改 `chat/_build_payload/_format_messages`） |
| `src/nexuscli/agent/agent.py` | 修改 | react 循环每轮**两个快照点**写 checkpoint；max_tokens+tool_calls 合成 is_error 结果；`Agent.run` 生成 `journal_run_id` 并支持恢复时沿用（`resume_run_id`/初始 usage 注入接缝）；warning 事件增可选键 `checkpoint_id`；error 事件增可选键 `error_code="llm_error"` |
| `src/nexuscli/agent/query.py` | 修改 | `query()` 增可选参数 `tool_journal`/`journal_run_id`，透传进其自建的 `ToolContext`（query.py:41-46）——plan 任务与 team react worker 的唯一注入通道（评审新增行） |
| `src/nexuscli/agent/plan_execute.py`、`agent/orchestrator.py`、`agent/query_engine.py` | 修改 | F11 终态聚合点换 typed outcome；`PlanCheckpointMissing` 类型化异常取代裸 ValueError；plan checkpoint 载荷增 run_id；`_execute_task_stream`（plan_execute.py:323-334）与 `_execute_worker`（orchestrator.py:174-185）调用 `query()` 处传注入参数 |
| `src/nexuscli/session/store.py` | 修改 | `_message_to_dict/_message_from_dict` 携带 `thinking_blocks` |
| `src/nexuscli/runtime/tasks.py` | 修改 | `TaskRecord` 增 `run_id` 列（既有迁移惯例） |
| `src/nexuscli/entrypoints/repl.py` | 修改 | 新增 `/turn-resume` 命令（纯新增分支，不动既有命令） |
| `pyproject.toml`、`uv.lock`、`.github/workflows/ci.yml` | 修改 | F13 配置、锁文件再生与 CI job |
| `src/nexuscli/runtime/api.py` | 修改（仅内部） | **端点面与响应契约不改**（01/02 工作台线已定稿）；`_run_task`（api.py:260-282）内部构造 QueryEngine/ToolContext 处注入 journal 与 `TaskRecord.run_id`——内部实现改动，不属于接口设计，与 F11 的「不改 Runtime API」口径以此精确表述为准 |

### 单用户裁剪声明（对标 kitagent 的取舍，写进交付 docstring）

不移植 `session_capabilities` 能力令牌/恢复租约/epoch CAS 整套多租户外壳（recovery.py:69-75、
554-625、785-979）：NexusCLI 是单用户 CLI + 本地 SQLite，`claim_tool` 的 `BEGIN IMMEDIATE`
事务（对标 recovery.py:207-218）已提供进程间互斥；fail-closed、幂等重放、类型化冲突、只存哈希
四个语义原样保留。`runs` 表（recovery.py:33-49）不建——run 状态的真相归 F10 的回合 checkpoint
与 plan checkpoint，日志只管工具调用本身，不做两套状态真相。

### 诚实边界汇总（交付物文档必须携带）

1. F9 的强保证只有 id 级精确重放（且仅 `/turn-resume` 按 persisted pending 调用重放的路径）；
   `/plan --resume`、team react worker、后台 lease 恢复是 hash 级**尽力去重**——LLM 重生成调用
   的参数漂移会绕过它，不承诺 exactly-once（见「业务逻辑」F9 第 5、6 条）。
2. F10 的 Ctrl+C 仍是中止；checkpoint 是「最近完整轮」快照，不是中断瞬间快照。
3. F10 checkpoint 明文落盘（含工具结果中的文件内容），沿用 plan_runs 口径。
4. F11 本轮只有 `collaborative_interrupt`/`budget_exhausted` 两个真实挂起生产者；
   `approval_pending` 生产者接线是挂账——F6 审批桥为同步阻塞回调、无挂起出口，异步化之前
   不接（见「业务逻辑」F11）。Runtime API 的端点面与响应契约不在本轮半径内（`_run_task` 内部注入
   journal/run_id 除外，见修改面清单）。
5. F12 的静默重放存在显示层前缀跳变；OpenAI-compatible 路径不获同款重试（挂账）。
6. F13 不承诺本轮全仓 mypy 清零；排除清单只减不增；基线数字 100 错/30 文件（2026-10-25）。
7. 全程不引入 LangGraph 或其他新运行时依赖；`tool_journal`、`turn_checkpoint` 只用标准库
   `sqlite3`/`json`/`hashlib`。

## 验收钩子

pytest 环境由 tests/conftest.py:19-37 的 autouse fixture 重定向 HOME 与 USERPROFILE——新增测试
一律不得绕开（凡 `Path.home()` 解析的 journal/checkpoint 路径自动落在临时目录）；渲染类环境变量
由 conftest.py:13-16 的 fixture 清除。新测试一律放 `tests/` 平铺，命名跟随现有惯例。

### F9 `tests/test_tool_journal.py`（新建）

- claim 四分支：无记录 → 插入 started 且 handler 执行；已有 completed → handler 执行计数为 1
  （用计数 handler 断言不再执行）且返回内容等于序列化结果；手工把行改为 started → 请求得到
  is_error 且内容含 `[outcome_unknown]`；同 tool_use_id 不同 `input_hash` → 断言内容含
  `[tool_invocation_conflict]` 且 store 层异常类型为 `ToolJournalConflictError`。
- 约束：构造 `status='completed' AND serialized_result IS NULL` 的插入被 SQLite CHECK 拒绝；
  旧版本 `journal_schema` → `ToolJournalSchemaError`。
- 输入哈希：`hash_tool_input({"a":1,"b":2})` 与键序颠倒的同一 dict 哈希一致。
- 执行器集成（id 级精确重放）：`ToolExecutor.execute_all` 两次注入**同 id** 的 `write_file` 调用，
  断言第二次结果来自复用、目标文件只被写一次、第二条审计记录 approver 为 `journal-reuse`；
  只读 bash 命令（`git status`）不产生日志行。
- hash 级尽力去重（评审新增）：同 `run_id` 下两次**不同 id、同 `input_hash`** 的 `write_file`
  调用（模拟恢复后 LLM 重生成），断言第二次复用不执行；同 run_id 不同 `input_hash` 的调用
  照常执行（未命中不去重）。
- 命令：`uv run pytest tests/test_tool_journal.py -q`，期望全绿。

### F10 `tests/test_turn_checkpoint.py`（新建）

- 写入（走真实循环，不手工构造，评审修正）：假 LLM 客户端（参照 tests/test_agent_turns.py:30-48
  的 `_AlwaysToolUseClient`/`_NaturalEndClient` 做法）驱动 react 循环，断言每轮先产生 in-flight
  快照（pending_tool_calls 非空、`run_id` 字段在）再被轮末快照覆盖（pending 为空）；执行中途中断
  （fake executor 抛错后停止）→ 磁盘上最后一份是 in-flight 快照且含本轮原始 tool_use_id。
- 降级：截断的 JSON 文件、字段缺失的文件 → `load` 返回 None。
- 恢复（id 级去重）：用上一步真实产生的 in-flight checkpoint 走 `/turn-resume` 路径——
  journal 预置行**使用 checkpoint 载荷里的 `run_id`**（评审修正：run_id 恢复时沿用，测试因此
  可写）与 checkpoint 内的原始 tool_use_id：预置 `outcome_unknown` → 断言该调用未被 handler
  执行、结果为 is_error；预置 `completed` → 断言复用且 handler 计数不增。
- usage/台账口径（评审修正）：断言续跑 done 事件的 usage = checkpoint.usage_so_far + 续跑增量；
  模拟 REPL 层确认被中断回合从未 `append_usage`、续跑完成后台账恰新增一条（无双计）。
- 分工表回归：`tests/test_plan_checkpoint.py` 全量重跑防回归。
- 命令：`uv run pytest tests/test_turn_checkpoint.py tests/test_plan_checkpoint.py -q`。

### F11 `tests/test_engine_result.py`（新建）

- 折叠：done 事件流 → `RunOutcome(status="success")` 且 usage/cost 透传；error 事件 →
  `status="failed"` 且 `error.code` 命中码表（分类走发射点 `error_code` 键 + `classify_error`
  兜底两条路都测：带键事件直接透传码，裸异常经 classify 映射）；max_turns 告警 →
  `RunSuspension(reason="budget_exhausted")`，checkpoint_id 两档断言（评审修正）：事件带
  `checkpoint_id` 键（F10 已落地）→ 断言非空；不带键（F11 先行的中间态）→ 断言字段为 None
  且折叠不抛；plan `plan_interrupted` → `reason="collaborative_interrupt"`。
- 兼容回归：`ask_complete` 对 error 事件仍 raise（断言既有语义未被改动），
  `tests/test_query.py` 全量重跑。
- 命令：`uv run pytest tests/test_engine_result.py tests/test_query.py -q`。

### F12 `tests/test_anthropic_client.py`（追加）与 `tests/test_agent_turns.py`（追加）

- 既有 fake stream 基建直接复用（monkeypatch `httpx.AsyncClient.stream`，
  test_anthropic_client.py:44-53；`<api-key>` 占位符纪律不变）。
- 重试：fake stream 第一次抛 `httpx.RemoteProtocolError`、第二次成功 → 断言最终 message_end/usage
  只出现一次、第二次尝试的 delta 未重复 yield、`asyncio.sleep` 收到 1.0（monkeypatch 计时）；
  三连断线后第四次成功 → 断言成功且 sleep 序列为 1/2/4（评审修正：4 次尝试兑现 master 的
  1/2/4s 口径）；四连断线 → 错误事件且尝试数=4；`HTTPStatusError` → 尝试数=1（语义错误不重试）。
- cache_control：捕获 payload，断言 `system` 为块数组且带 `cache_control: {"type": "ephemeral"}`、
  最后一个 tool 带 cache_control、其余 tool 不带。
- thinking 回传：构造带 `thinking_blocks` 的 assistant `Message` → 断言 payload 的 assistant
  content 首块为 `{"type":"thinking","thinking":…,"signature":…}` 且逐字等于输入。
- max_tokens 合成（react 层，放 test_agent_turns.py）：假客户端返回 `stop_reason="max_tokens"`
  + 一条 tool_call → 断言该调用未执行、历史中 tool 消息 `is_error` 成立、配对完整。
- 命令：`uv run pytest tests/test_anthropic_client.py tests/test_agent_turns.py -q`。

### F13 静态检查钩子

- 摸底命令（已在本会话执行，基线数字来源）：`uv run --with mypy mypy src/nexuscli` →
  `Found 100 errors in 30 files (checked 122 source files)`。
- 配置后本地门：`uv run mypy src/nexuscli` → exit 0（mypy 进入 dev extras 后为标准命令）；
  `uv run pytest tests/test_tool_journal.py tests/test_turn_checkpoint.py tests/test_engine_result.py -q`
  顺带证明三个新模块在清单外通过类型检查。
- CI 门：mypy job 执行 `uv sync --extra dev --frozen && uv run mypy src/nexuscli`，红 = 清单外
  新错误（清单只减不增的机械保证）；pyproject 改动与 `uv lock` 再生须同一提交（见数据模型
  F13 的锁文件纪律）。

### 通用 smoke（本轮各特性完成后必跑）

- `uv run ruff check . && uv run ruff format --check .` → 全绿（本会话实测基线：`uv run ruff check .`
  输出 `All checks passed!`）。
- `uv run nexuscli --help` → exit 0（本会话实测 exit=0，47 行输出）；F10 落地后追加断言
  帮助文本含 `/turn-resume` 所在命令面不变。
- 真实恢复链路的端到端 smoke 不强求无 key 环境：`/plan --resume` 与 `/turn-resume` 的去重行为
  以 pytest 为准（上文各节），`uv run nexuscli -p "…" --yes` 类真实链路属可选人工验证，不作
  CI 门槛。
