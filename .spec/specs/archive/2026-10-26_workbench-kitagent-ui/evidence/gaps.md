# 对照差距清单（task_0002 产出，基线 = evidence/baseline/）

对照方法：两侧 demo 栈真服务 + Playwright 截图（scripts/ui_compare_shots.py）。
左列 = kitagent 基线页，右列 = 本仓现状页。

## 布局与骨架

| # | kitagent（baseline/kitagent-*.png） | 本仓现状（baseline/nexuscli-*.png） | 处置 |
|---|---|---|---|
| G1 | sidebar：品牌区/新建会话/工作会话列表/底部徽标 | 无侧栏，三个裸 tab 按钮平铺页首 | task_0003 |
| G2 | topbar：面包屑 + 实时连接徽章 + 面板开关 | 无 | task_0003 |
| G3 | workspace 双栏：聊天主面板 + 右侧检查器 | 单列长页面，转录沉底 | task_0003 |
| G4 | statusbar：引擎/工作目录/状态 | 无 | task_0003（本仓无 /status 端点，改为连接态 + 会话标识，见 A8） |

## 聊天面板

| # | kitagent | 本仓现状 | 处置 |
|---|---|---|---|
| G5 | 欢迎页：eyebrow + 大标题 + 示例任务按钮 | 无 | task_0004 |
| G6 | user/assistant 气泡 + markdown 渲染 + 代码块复制 | `<pre>` 裸文本，`##` 原文裸奔 | task_0004 |
| G7 | composer：Enter 发送/Shift+Enter 换行/IME 合成保护/草稿持久化/键位提示行 | 无键位提示、无 IME 保护、无草稿 | task_0004 |
| G8 | 运行中 inline 状态行 + 「有新内容」跟随按钮 | 无 | task_0004 |
| G9 | composer footer：模型点 + 权限模式控件 | 模式下拉裸排 | task_0004（权限模式控件本轮不做，见「挂账」） |

## 检查器

| # | kitagent | 本仓现状 | 处置 |
|---|---|---|---|
| G10 | 执行过程 tab：环境卡 + run 分组工具行 + 选中详情 + 空态 | 工具调用混在转录 pre 里 | task_0004 |
| G11 | 结果 tab：运行报告（统计/导出/复制结论/证据列表） | 无（仅 REPL 回放有导出按钮） | task_0004 |
| G12 | 审批卡：图标 + 状态 + 来源 + 参数 pre + 有效期 + 拒绝/允许 | 功能等价（Reason 行在），视觉为零 | task_0004 |

## 特有视图融入

| # | 说明 | 处置 |
|---|---|---|
| G13 | REPL 会话回放、任务面板、用量统计在 kitagent 无对应物，视觉为零 | task_0003/0004：并入侧栏 + 同一设计语言重绘 |

## 认证门槛

| # | 说明 | 处置 |
|---|---|---|
| G14 | gate 页死胡同：只提示「从服务启动日志找链接」；口令一次性且服务在后台跑时用户拿不到链接（2026-10-26 用户实测撞上） | task_0006：粘贴换发 + 服务端链接落盘 0600 |

## 接线

| # | 说明 | 处置 |
|---|---|---|
| G15 | A12 的 useWorkbench（bootstrap/pending 恢复/SSE 重连/seq 折叠）未接入 App；App 仍是 2s 轮询 + 独立 useEventStream 双轨 | task_0003 接线，轮询仅保留 approvals |
| G16 | state.ts 未映射 turn.started：聊天面板拿不到用户消息（回放/刷新场景） | task_0003：state.ts 增量映射（seq 逻辑不动） |

## 适配说明（有意差异，非差距）

- A8 statusbar 数据面：kitagent 有 GET /status（core 引擎/sandbox/cwd）；本仓 API 面无
  此端点（useWorkbench 头注已定稿「无 GET /status」），statusbar 改显：SSE 连接态 +
  当前会话 id + 事件数。
- A9 权限模式控件（kitagent PermissionControl，read_only/ask/auto）：本仓后端
  GET/PATCH /v1/workbench/permissions 已在（A6），前端本轮不接线——挂账为 follow-up，
  不阻塞本轮验收。
- 审批动词保留本仓 01 定稿：批准/拒绝（不引 kitagent 的 deny_once/allow_session 语义）。

## 自迭代记录

### iter1（evidence/iter1/）发现 → 修复
- 通知「turn 完成」误用红色错误条 → 新增中性 notice-banner 样式（App.tsx + index.css）
- Composer 在 pending 未决时也渲染「只读态（无 CSRF 凭证）」误导文案 → 移除该 span，
  状态说明统一走占位符（disabledHint）
- 新发起的线程未进侧栏「工作会话」（startTurn 缺 rememberThread）→ useWorkbench
  startTurn 建线程后调用 rememberThread(title=任务首行 80 字符)

### iter2（evidence/iter2/）复核
- 侧栏出现「检查样例 Python 仓…」且高亮 active ✓
- 通知条中性色 ✓；composer 无误导文案 ✓
- 任务/用量/回放面板与 kitagent 同一设计语言 ✓

### 遗留（非 UI 差距，如实记录）
- 离线 demo provider 的收尾文本在「## 检查报告」前缺空行，markdown 里渲染为行内
  字面量（内容级问题，源在 offline demo 文案，不在前端渲染链；kitagent demo 文本
  自带 \n\n 结构）。不阻塞验收，不动后端 demo 内容。
