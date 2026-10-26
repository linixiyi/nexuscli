# Design — NexusCLI Workbench

<!-- impeccable:design-schema 1 -->

## Mode

**Operate**（工作台）：访客在完成"驱动 coding agent 任务"这件事。可扫读性、一致性、
真实使用场景优先于表达力；品牌活在精确细节里。信息密度与耐看度优先（与 PRODUCT.md
的终端主表面同一用户诉求）。

## World

浅色单世界工作台，2026-10-26 自 kitagent（MIT, zed123214/kitagent）整链移植后按本仓
语义增删（回放/任务/用量面板与认证 gate 为本仓重绘）。视觉语言：白/近白分层表面 +
1px `--line` 分隔（不靠投影堆层级）+ 蓝色 `--blue` 唯一强调色 + 金色调审批卡
（危险/需决策的独特语义）。**Refinement 模式**：此世界是被承诺的 incumbent，
不做无人工评审的重设计。

## Tokens

- 色板：`--blue #3665e6`（唯一强调/hover/focus）、`--muted #8590a3`（次级文本）、
  `--line #e7eaf0`（分隔线）、`--green #3968`（成功语义）、`--amber #af791e`
  （等待/警示语义）；表面 `#fff` / `#f7f8fa` / `#fdfdfe` 三层。语义色之外不引入
  新色相；透明度着色用 8 位 hex（如 `#3665e614`）。
- 字体：系统栈 `Inter, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif`。
  **权衡记录**：Inter 无 @font-face（零外部资源约束，PRODUCT.md），实际回落系统
  字体；impeccable detect 会报 overused-font，属于已知有意取舍，不是待办。
- 字号：正文 14px / 行高 1.6；次级 12px（.muted）；标签 11px + 宽字距
  （.eyebrow，本世界自 kitagent 继承的分节标签系统，功能是面板内扫读锚点，
  非 marketing kicker——保留为 incumbent 身份的一部分）。
- 圆角：控件 7px；卡片/面板更大（继承 kitagent 比例）；pill 只用于小控件。
- 间距：8 的倍数为主；标题上方间距大于下方；面板内边距 16-19px。

## Depth & surfaces

层级用 1px 边框 + 近白底色差表达；box-shadow 只做柔和投影（带偏移与模糊，
如 `0 3px 10px #23354f0e`），不做 zero-offset 光晕，不做硬偏移块影。
活动态指示器用 2-3px 左边框（列表活动项）——功能性模式，保留。

## Browser surfaces（本文件新增的承诺）

文本选区用主题蓝淡染（`::selection`），输入 caret 主题蓝，数字上下文
（`.statusbar` / `.data-table` / `.stat-card` / `.detail-meta`）一律
`font-variant-numeric: tabular-nums`，链接下划线偏移 2.5px。
滚动条已细窄主题化（`scrollbar-width/color`）。

## Components

- 布局骨架：sidebar（会话/任务导航）+ topbar + chat 主列 + inspector 右栏 +
  statusbar 底栏；响应式断点 1279 / 1023 / 600。
- 认证 gate 卡片：一次性口令/粘贴换发；错误就地显示、表单保留（U1 回归锚定）。
- 审批卡：金色调 + 需决策语义；Markdown 渲染走 .markdown 排版链。
- 空态：.panel-empty（图标 + h3 + p）统一三件式。

## Motion

极简：按钮/面板 background、border-color 0.15s 过渡；无入场动画系统。
慢等待提示（W1）是唯一"时间感知"动效语义，行为由 e2e 锚定，不改观感契约。

## Voice

界面文案中文为主（按钮"新建会话"、占位"粘贴一键链接或口令"），错误文案说清
问题与恢复路径；e2e 断言的具体文案是行为契约，改文案必须同步 e2e。

## Behavior pins（不可回退）

慢等待反馈（W1）、会话可辨识（W2）、报告证据逐工具行（U6）、坏口令可恢复（U1）、
刷新 csrf 自签恢复（U2）、副标题用户文案（U7）——`frontend/e2e/workbench.spec.ts`
与 `.spec/specs/archive/2026-10-27_workbench-ux-*` 为准。
