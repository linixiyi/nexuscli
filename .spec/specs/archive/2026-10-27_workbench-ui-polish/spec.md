# 夜间④Workbench UI 美化 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：用 impeccable 技能为 frontend/ 工作台做 refinement 打磨——提炼设计语言成 DESIGN.md、补齐"浏览器表面"工艺层（选区/caret/表格数字/下划线偏移），行为零回退
- **目标用户**：workbench 的真实使用者（开发者本人）与后续设计贡献者
- **核心价值**：设计语言首次成文（DESIGN.md），浏览器自带部件归入设计系统，行为锚全绿

## 2. 假设与待确认

### 2.1 已确认事实
- frontend 是 2026-10-26 自 kitagent 移植的完整设计语言（浅色、--blue 唯一强调、1px 分隔线体系），属 Refinement 场景（refinement preserves）而非重设计
- impeccable launcher 仅带 detect 扫描器；document/critique/polish 为提示工作流，按 reference 手工执行
- e2e 有真服务管线（web_validation_server.py + 离线 provider），可做真实截图巡检

### 2.2 关键假设
- detect 报的 overused-font(Inter) 为已知有意取舍（零外部资源约束、无 @font-face 实际回落系统字体），记录进 DESIGN.md 而非盲改
- .eyebrow 分节标签与 border-left 活动指示器是 incumbent 身份的功能性模式，refinement 保留

### 2.3 待确认问题
- [x] 截图巡检是否发现需第二轮修复的缺陷 -> 结论：未发现（五张截图：桌面/任务/用量/门/移动门均达标），按 bounded passes 停手

### 2.4 可选解释与取舍
- 当前选择：截图用一次性 node 脚本（不入库），巡检结论落包内 visual-review.md -> 截图属过程证据，不提交二进制
- 当前选择：CSS 修复只做浏览器表面层（::selection/caret/tabular-nums/underline offset）-> 零行为风险的最高确定收益；更大视觉改动留人工评审

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] DESIGN.md：工作台设计语言成文（mode/tokens/depth/components/motion/voice/行为锚）
- [x] index.css 浏览器表面段：::selection 主题蓝、caret 主题蓝、数字上下文 tabular-nums、链接下划线偏移
- [x] 验证：vite build + e2e 11/11 + web/runtime pytest 全绿；真实服务截图巡检一轮

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 换字体/重设计/动画系统（需人工评审）、TUI 渲染层（PRODUCT.md 的另一表面）

## 4. 最小实现路径
- 最简单可行方案：impeccable context → detect → craft-floor 静态检查 → document（DESIGN.md）→ 一轮 CSS 批修 → 真服务截图巡检一轮 → e2e 行为锚
- 暂不引入：webfont 资源、设计 token 抽取（extract）、动画
- 不做的抽象/配置化：不建 surface brief 文件（单一表面，DESIGN.md 足够）

## 5. 技术决策
- 技术栈：impeccable detect + 手工 critique + playwright 截图
- 证据协议：evidence: runs
- 本轮允许改动：DESIGN.md（新增）、frontend/src/index.css、包内 visual-review.md
- 本轮不应触碰：frontend/src/**/*.tsx（行为与结构零改动）、e2e 用例
- Git integration branch：`spec/2026-10-27_workbench-ui-polish`

### 5.1 验证策略
- 范围级别：package
- 变更对象：仅 CSS 表现层与新增文档；组件结构零改动
- 快速检查：`cd frontend && npm run build`
- 集成检查：task_0003 e2e 全套（行为锚 W1/W2/U1/U2/U6/U7）
- 全项目检查：仅在收尾或升级触发时执行——web/runtime 相关 pytest 即本轮全项目面（CSS 不触 Python 面）
- 升级触发：e2e 或巡检发现行为/视觉回归时，回写本包并本轮修复

## 6. 成功标准与验证方式
- [x] DESIGN.md 成文且含设计语言与行为锚 -> verify: task_0001
- [x] 浏览器表面段落地且构建通过 -> verify: task_0002
- [x] e2e 11 用例全过（行为零回退）且 web/runtime pytest 绿 -> verify: task_0003
- [x] 截图巡检一轮完成且结论记录在包 -> verify: task_0004

## 7. 风险与约束
- 风险点：CSS 全局选择器影响既有组件 -> 缓解：只加新规则不改既有规则；e2e 全套护航
- 风险点：tabular-nums 改变正文视觉宽度 -> 缓解：只作用于数字密集上下文（.statusbar/.data-table/.stat-card/.detail-meta）
