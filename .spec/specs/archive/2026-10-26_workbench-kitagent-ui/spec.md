# Workbench 前端对齐 kitagent 页面 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 NexusCLI 可视化工作台从「无样式的极简移植」重构为与 kitagent
  页面同一设计语言的工作台（侧栏 + 聊天主面板 + 执行检查器 + 状态栏），并以
  「运行真实页面 → 截图对照 → 修差距」的自迭代闭环收敛视觉与交互；同时修掉
  认证门槛页的死胡同体验。
- **目标用户**：在浏览器里观察/驾驶 NexusCLI Agent 执行的本机用户。
- **核心价值**：工作台从「能看」变成「好用」：会话/执行过程/审批/报告按
  kitagent 的信息架构呈现，进入体验不再依赖翻服务日志。

## 2. 假设与待确认

### 2.1 已确认事实
- kitagent 源码 /tmp/kitagent-src 可用（MIT），前端为完整 UI：App.tsx 1072 行 +
  styles.css 1491 行 + PermissionControl 209 行，布局为 sidebar / topbar /
  chat+inspector workspace / statusbar
- NexusCLI 前端现状：App.tsx 160 行 + index.css 194 行，tab 式三个裸面板；
  A12 已移植的 state.ts（SSE 帧折叠）与 useWorkbench.ts（bootstrap/pending 恢复/
  SSE 重连）**尚未接入 App.tsx**
- NexusCLI 后端 API 面齐全：threads/turns/events(+SSE stream)/sessions/
  usage/summary/approvals/workbench/permissions(PATCH)/tasks；离线 demo 栈
  `scripts/web_validation_server.py`（NEXUSCLI_OFFLINE_DEMO=1）可端到端跑 UI
- bootstrap 口令一次性（api.py `_handle_bootstrap`：已用即 403），门槛页当前
  只提示「从服务启动日志找一键链接」——服务在后台/他人终端跑时用户拿不到链接
  （2026-10-26 用户实测撞上，截图为证）
- kitagent 前端依赖 lucide-react / react-markdown / remark-gfm /
  @assistant-ui/react；NexusCLI package.json 目前只有 react/react-dom
- 本仓既有规范：照搬保留「Ported from kitagent (MIT, zed123214/kitagent)」出处注释

### 2.2 关键假设
- @assistant-ui/react 不移植：它服务于 kitagent 的 live-runtime 消息模型，而
  本仓数据模型是「SSE 帧折叠 + 回放合并」（state.ts 已定稿），引入它反而要
  双轨维护；用自有状态层实现同样的视觉结构
- kitagent 的 styles.css 设计 token（配色/圆角/间距/布局断点）可直接移植后
  按本仓语义改名，前景色取向（不写死背景）保持
- /tmp/kitagent-src 在本轮实现期间保持存在；丢失则从 GitHub 重拉

### 2.3 待确认问题
- [x] 用量/任务面板在 kitagent 没有对应物，放哪？→ 结论：保留为工作台内的
  视图（侧栏底部入口切换），用 kitagent 的面板语言重绘，不砍功能

### 2.4 可选解释与取舍
- 当前选择：布局/样式/交互模式照搬 kitagent，数据层用本仓 A12 成果 -> 两仓
  API 面不同（sessions 回放、tasks、usage 是本仓特有），语义层不可照搬；
  视觉层可照搬且有先例（A1-A14 均为 Ported-from 模式）
- 当前选择：认证门槛加「粘贴一键链接/token」输入框 + 服务端把一键链接落盘
  state 目录 -> 一次性口令语义不变（kitagent 对齐），但用户不再必须翻日志

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 布局骨架对齐：sidebar（品牌/新建会话/会话列表/底部入口）+ topbar（面包屑/
  连接徽章/检查器开关）+ workspace（chat + inspector 双栏）+ statusbar
- [x] 聊天面板对齐：欢迎页（含示例任务按钮）、user/assistant 消息（markdown
  渲染 + 代码块复制）、composer（Enter 发送/IME 合成保护/草稿持久化/模式选择/
  发送按钮/键位提示）、「有新内容」跟随按钮
- [x] 执行检查器对齐：执行过程/结果双 tab、审批卡（参数 pre、有效期、拒绝/
  允许、五态状态）、工具时间线（run 分组 + 工具行 + 选中详情）、运行报告
  （统计 + 导出 markdown）、空态插画页
- [x] 回放与特有视图融入：REPL 会话回放、任务面板、用量统计用同一设计语言重绘
- [x] 认证门槛 UX：gate 页可粘贴一键链接/token 换发；服务端启动时把一键链接
  写入状态目录（0600）；门槛页文案给出可执行的恢复路径
- [x] useWorkbench（A12 成果）接入 App：SSE 直连 + 断线重连 + pending 幂等恢复

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 不移植 @assistant-ui/react、不引入 WS（本仓 SSE 定稿不变）
- 不改后端 API 契约（bootstrap 一次性语义、幂等门、CSRF 均不动；链接落盘是新增
  文件不改协议）
- 不做移动端专属优化（保留 kitagent 的响应式断点行为即可）
- 不动 TUI/REPL 侧任何代码

## 4. 最小实现路径
- 最简单可行方案：以 kitagent styles.css 为底本移植设计 token 与组件样式，
  App.tsx 按 kitagent 布局重写，视图组件（Transcript/Composer/ApprovalCard/
  Sessions/Tasks/Usage）在其语义内重构，state.ts/useWorkbench.ts 接线
- 暂不引入：组件库（shadcn 等）、CSS 框架、状态管理库、@assistant-ui/react、
  虚拟滚动（长转录先沿用法线滚动）
- 不做的抽象/配置化：不做主题切换配置（沿用浏览器深浅色前景色取向）、不做
  i18n、不把视图组件做成可插拔
- 自迭代机制：用 `scripts/web_validation_server.py`（本仓）与 kitagent demo 栈
  （/tmp/kitagent-src，agentrt-web + demo core）各起真服务，浏览器截图两侧
  关键页面，按页面逐块对照修差距，直至对照清单收敛

## 5. 技术决策
- 技术栈：React 19 + Vite（不变）；新增 react-markdown + remark-gfm +
  lucide-react；样式纯 CSS（移植 kitagent styles.css）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run
  真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify
  命令不得自引用本包 check 门禁）
- 本轮允许改动：frontend/**（App/views/api/state/sse/styles/index.html）、
  frontend/package.json、scripts/web_validation_server.py（如需给 e2e 暴露
  gate 场景）、src/nexuscli/runtime/api.py 与 web_session（仅限「一键链接落盘」
  最小面）、docs/workbench.md
- 本轮不应触碰：runtime API 端点契约与响应形状、审批/幂等/CSRF 语义、
  TUI/REPL/engine 代码、tests 里与前端无关的断言面
- Git integration branch：`spec/2026-10-26_workbench-kitagent-ui`

### 5.1 验证策略
- 范围级别：package
- 变更对象：frontend 构建产物与托管面、runtime api 的链接落盘行为、web
  validation server、workbench 文档
- 快速检查：`cd frontend && npm run build` + `uv run python -m pytest tests -q -k "workbench or runtime"`
- 集成检查：`cd frontend && npx playwright test`（真服务 e2e，覆盖 bootstrap/
  发起 turn/会话列表/任务面板）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或
  package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

### 5.4 编排策略
- route: review
- immediate blocker: App.tsx 重构与 useWorkbench 接线（task_0003/0004 主链路）留在主线程
- ownership: 基线截图脚本（task_0002）、认证门槛服务端最小面（task_0006）可作
  bounded 切片交 in-process sidecar；App/views 主实现主线程独占
- waiting strategy: task_0002 基线先收敛再动 task_0003；check 轮等待 review lane 结论
- verification gate: build + playwright e2e + pytest（workbench/bootstrap 面）
  全绿，风险标签「认证/信任边界」触发 check 轮只读 reviewer sidecar 终局换眼

## 6. 成功标准与验证方式
- [x] 前端构建通过、既有 e2e 基线全绿 -> verify: task_0001
- [x] 两侧对照基线截图落盘（kitagent demo 栈 + 本仓现状），对照清单成文 -> verify: task_0002
- [x] 布局骨架/聊天面板/检查器/特有视图按 kitagent 设计语言落地且 e2e 通过 -> verify: task_0004
- [x] 自迭代收敛：终态截图与 kitagent 对照清单逐项闭合 -> verify: task_0005
- [x] 认证门槛可自救：粘贴换发 + 链接落盘（0600）有测试证据 -> verify: task_0006

## 7. 风险与约束
- 风险点：1491 行样式移植带入 kitagent 特有语义（WS 状态、权限模型标签）导致
  死样式 -> 缓解：移植时按本仓事件/状态名改名，build 后用 e2e 页面逐块核对，
  不引入无对应 DOM 的样式段
- 风险点：@assistant-ui 缺位导致聊天交互（滚动跟随/IME）回归 -> 缓解：手写
  follow-scroll 与 composition 保护（kitagent 同款逻辑，逐行移植该部分）
- 风险点：demo 栈截图依赖浏览器环境 -> 缓解：用本机 Playwright chromium（e2e
  已有依赖）；脚本缺失时任务按 manual 证据降级并如实记录
