# Workbench UX 实测迭代 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：以 PM/真实用户视角实测工作台完整旅程（18 张截图证据；步骤编号到
  19，09-tool-detail 为条件步骤——tool-row 不可见即跳过），修掉实测
  发现的 2 个 P0 与 3 个 P1 问题、1 个 P2 文案问题，让「第一次用的真人」能走通
  进入→发任务→审批→刷新恢复的完整闭环。
- **目标用户**：本机拉起 workbench 的真实用户（非开发者视角）。
- **核心价值**：从「演示能跑」到「真人能用」：错误可恢复、刷新不死局、状态说实话。

## 2. 假设与待确认

### 2.1 已确认事实
- 实测方法与证据：web_validation_server 离线 demo 真服 + Playwright 18 张旅程截图
  （09-tool-detail 条件步骤，两轮实跑均 18 张；evidence/baseline/，findings.md 逐条引用）
- U1：redeem 失败路径不还原 auth phase（bootstrapping 卡死，02 截图）
- U2：csrf 仅内存 + 口令一次性 → 刷新即只读死局；「用一键链接重新进入」指向死链
- U3：useWorkbench 已产「线程不存在」streamError 但无呈现位（只进状态栏小字）
- U4：run-inline 只有 busy 一态；审批挂起仍显示「执行中」
- U5：toLocaleString() 未显式 locale（en-US 格式泄漏，07 截图）
- U7：任务/用量面板副标题是端点级实现细节文案；e2e 断言依赖「每 2s 轮询 GET
  /v1/tasks」字样，改动需同步
- 真实下载导出已实证工作（journey capture: downloaded）

### 2.2 关键假设
- U2 的服务端解法（凭有效会话 cookie 重签 csrf）不降低安全防线：csrf 防的是跨源
  伪造；已持有效会话 cookie 的同源 JS 本就能发请求，重签只是恢复其变更能力；
  kitagent 语义中 csrf 本就是会话级衍生秘密（非一次性）
- demo 栈（离线 provider）足以复现并验证全部 UX 修复（U1/U3 不依赖真实 LLM）

### 2.3 待确认问题
- [x] U2 是否削弱安全？→ 结论：否（见 2.2）；重签端点只认有效会话 cookie，
  无 cookie 仍 401；重签后旧 csrf 值被替换（单值语义不变）
- [x] U6/U8（报告证据列表弱、无 DEMO 横幅）？→ 结论：挂账后续包，不进本轮

### 2.4 可选解释与取舍
- 当前选择：U2 用「重签端点」而非「csrf 存 sessionStorage」-> 会话语义干净
  （信任根仍是 cookie），不受存储清除/多标签页影响；kitagent 的 WS 模型无此问题
  是因为连接即会话，SSE 模型需要显式恢复通道
- 当前选择：U4 用 pending approval 轮询结果驱动文案（approvals 非空 → 等待审批态）
  而非新事件 -> 零协议改动

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] U1：redeem 失败还原 waiting 态并呈现错误（口令无效/已用给恢复指引），表单可重试
- [x] U2：服务端 POST /v1/workbench/reissue-csrf（持有效会话 cookie → 重签 csrf；
  无 cookie 401；重签替换旧值）+ 前端 initialize 探测到 canPost=false 时自动重签
  恢复，刷新后完整可写；docs/{workbench,runtime-api}.md 鉴权小节同步
- [x] U3：线程不存在/连接错误的显著错误条（复用 notice/error-banner 呈现位，
  streamError 有内容即显示）
- [x] U4：审批挂起期 run-inline 显示「等待审批 · 请在右侧面板批准」（approvals
  轮询非空时），否则执行中
- [x] U5：审批卡挂起时间显式 toLocaleString("zh-CN")
- [x] U7：任务/用量面板副标题改用户视角文案，e2e 断言同步
- [x] 回归证据：修复后同旅程重截图（evidence/after/），U1/U2 用例前后对照

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- U6（报告证据列表强化）、U8（DEMO 横幅）挂账
- 不动 runtime API 既有端点契约（reissue-csrf 是新增端点）；不动 SSE/审批协议

## 4. 最小实现路径
- 最简单可行方案：U1/U3/U4/U5 纯前端小改；U2 一个新端点（websession.reissue +
  api.py 路由 + 测试）+ 前端 initialize 一次自动重签；U7 文案+e2e 断言同步
- 暂不引入：双提交 cookie 重构、多 csrf 值、sessionStorage 兜底
- 不做的抽象/配置化：重签不做频率限制（单用户本机服务）；错误条不做分级样式体系
- 修完用同一 journey 脚本重截图对照（02/11 两张是 U1/U2 的直接对照证据）

## 5. 技术决策
- 技术栈：不变（React/CSS + 标准库 http.server）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实
  执行并记录 runs.jsonl；无命令任务写 verify: manual: <理由>；verify 命令不得自引用
  本包 check 门禁）
- 本轮允许改动：frontend/src/**、frontend/e2e/workbench.spec.ts（U7 断言与新回归
  用例）、src/nexuscli/runtime/{api.py,websession.py}（reissue-csrf 最小面）、
  tests/test_runtime_hardening.py 或新测试文件、docs/{workbench,runtime-api}.md、
  scripts/ui_journey.py + scripts/ui_journey_runner.mjs（journey 脚本入库以便复跑；
  与原计划单文件 ui_journey.mjs 的偏差在此记账：py 入口管服务生命周期与 AGENTS.md
  进程纪律 killpg，mjs runner 管步骤截图与 pageerror 非零退出，plan 文件一次性口令
  用完即删——功能上比单文件形态更完整，交付时按此两文件为准）
- 本轮不应触碰：bootstrap 一次性语义、审批/幂等协议、SSE 事件契约、既有端点响应形状
- Git integration branch：`spec/2026-10-27_workbench-ux-iteration`（自
  spec/2026-10-26_workbench-kitagent-ui 链生；合并顺序：其先合入）

### 5.1 验证策略
- 范围级别：package
- 变更对象：gate/redeem 前端、reissue-csrf 端点、错误呈现位、文案、journey 脚本
- 快速检查：`uv run python -m pytest tests -q -k "workbench or bootstrap or runtime"`
- 集成检查：`cd frontend && npm run build && npx playwright test e2e/workbench.spec.ts`
  （含 U1/U2 新回归用例）+ journey 重截图对照
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（workbench 面
  增量 + 一个新增端点）；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或
  package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] U1：坏口令呈现错误且表单可重试（e2e 断言 + after 截图）-> verify: task_0001
- [x] U2：刷新后自动恢复可写（服务端测试 + e2e 刷新用例 + after 截图）-> verify: task_0002
- [x] U3/U4/U5：错误条/等待审批态/中文时间戳落地 -> verify: task_0003
- [x] U7：面板文案用户化且 e2e 同步 -> verify: task_0003
- [x] 全量门 + journey 重截图对照（after/）-> verify: task_0004

## 7. 风险与约束
- 风险点：reissue-csrf 被滥用于无 cookie 探测 -> 缓解：无有效 cookie 一律 401 且
  不泄露任何信息；测试覆盖
- 风险点：前端自动重签掩盖真只读场景（服务重启后 cookie 失效）-> 缓解：重签 401
  时保持 waiting 态并走 gate 流程

### 5.4 编排策略
- route: review
- immediate blocker: reissue-csrf 端点的安全语义（cookie 门/重签替换）与 e2e 回归
  用例由主会话验收，实现可并行
- ownership: lane A = task_0001（前端四修+e2e 回归+journey 入库）∥ lane B =
  task_0002（服务端端点+测试）文件面零交集；task_0003（文案+文档）在两 lane 后串行
- waiting strategy: task_0003 等双 lane 落地；check 轮 reviewer sidecar 只读复核
  reissue 面（认证/信任边界触发）
- verification gate: 全量 pytest + ruff 双绿 + e2e（含 U1/U2 新回归）+ journey 重
  截图（after/）与 findings.md 逐条对照
