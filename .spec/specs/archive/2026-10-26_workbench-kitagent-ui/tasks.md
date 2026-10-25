# Workbench 前端对齐 kitagent 页面 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：基线与依赖

- [x] task_0001 前端依赖与构建基线
  - boundary: 只改 frontend/package.json（新增 react-markdown / remark-gfm /
    lucide-react）与 lockfile；不碰任何源码
  - verify: `cd frontend && npm install && npm run build`（前置：Node ≥22；成功判定：
    tsc 无错误、dist/ 产出含新依赖 chunk；失败处理：依赖冲突时回退用 npm 安装
    具体版本并记录在任务注释）
- [x] task_0002 两侧对照基线截图与差距清单
  - depends-on: task_0001
  - boundary: 只新增 scripts/ui_compare_shots.py、scripts/ui_shots_runner.mjs
    （浏览器 runner，经 frontend 的 @playwright/test 依赖）与本包 evidence/ 下
    截图；不改业务源码
  - verify: `python3 scripts/ui_compare_shots.py --out .spec/specs/2026-10-26_workbench-kitagent-ui/evidence`（前置：
    /tmp/kitagent-src 存在且已 uv sync --extra web 并构建 frontend；脚本自起两侧
    demo 服务并 Playwright 截图 kitagent onboarding/欢迎/审批/报告 与本仓 gate/
    主视图/转录/任务/用量；成功判定：exit 0 且 evidence/baseline/ 下两侧 PNG
    齐全；失败处理：kitagent 栈起不来时 --side nexuscli 单侧降级并如实记录）

## 阶段二：UI 对齐实现

- [x] task_0003 布局骨架：sidebar + topbar + workspace + statusbar
  - depends-on: task_0002
  - boundary: frontend/src/{App.tsx,index.css,state.ts,state/localThreads.ts}、
    frontend/src/views/{Sidebar,ChatPanel,Composer,SessionsView,TasksView,
    UsageView}.tsx；e2e/workbench.spec.ts 断言随 DOM 结构同步更新；styles 以
    kitagent styles.css 为底本移植（保留 Ported-from 注释）；useWorkbench 接入
    App（轮询仅保留 approvals）；state.ts 增量映射 turn.started（seq 逻辑不动）；
    会话列表 = 本浏览器线程 + REPL 会话并入侧栏；任务/用量改为侧栏入口切换的视图
  - verify: `cd frontend && npm run build && npx playwright test e2e/workbench.spec.ts`（成功判定：
    build 零错误且 e2e 基线 5 条用例全绿（或按既有 skip 规则跳过并留说明）；失败
    处理：先看 e2e 报告截图定位渲染错误）
- [x] task_0004 聊天面板 + 检查器对齐
  - depends-on: task_0003
  - boundary: frontend/src/views/{TranscriptView,Composer,ApprovalCard,TasksView,
    UsageView,report,timeline}.tsx(ts)；markdown 渲染（react-markdown+gfm+代码块
    复制）、欢迎页与示例任务、composer（Enter/IME/草稿/模式/键位提示）、检查器
    （执行/结果 tab、审批卡、工具时间线、报告导出、空态）；Tasks/Usage 面板重绘
  - verify: `cd frontend && npm run build && npx playwright test e2e/workbench.spec.ts`（成功判定：
    同 task_0003 且无页面级 React 错误；失败处理：拆出复现交互路径后修复再跑）
- [x] task_0005 自迭代对照收敛
  - depends-on: task_0004
  - boundary: 只按截图差距改 frontend/src 样式与组件结构；重新生成 evidence 终态截图
  - verify: `python3 scripts/ui_compare_shots.py --out .spec/specs/2026-10-26_workbench-kitagent-ui/evidence --final`（成功判定：
    exit 0 且 --final 终态截图落盘；对照清单（evidence/gaps.md）逐项标注闭合；
    失败处理：清单未闭合项回本任务继续修，不带入 done）

## 阶段三：认证门槛 UX

- [x] task_0006 认证门槛自救：粘贴换发 + 链接落盘
  - depends-on: task_0003
  - boundary: frontend/src/App.tsx 的 gate 分支；src/nexuscli/runtime/api.py 与
    web_session 仅新增「启动时把一键链接写状态目录（0600）」最小面；对应测试；
    docs/workbench.md 鉴权小节同步
  - verify: `uv run python -m pytest tests -q -k "workbench or bootstrap"`（成功判定：
    新增断言「服务启动后状态目录存在 workbench-link.txt 且权限 0600、内容含
    #bootstrap=」通过，既有 bootstrap 测试不回归；失败处理：查 api.py 落盘路径
    与 HOME 双重定向 fixture 的交互）

---

**当前进度**：由脚本计算，无需手动维护

## 复审回写备注（2026-10-26 check 轮）

- task_0003/0004 boundary 所列 `views/SessionsView.tsx`、`views/TranscriptView.tsx`、
  `views/timeline.ts` 在实现中并入 `Sidebar.tsx` / `ChatPanel.tsx` / `ReplayView.tsx` /
  `derive.ts` 后删除；boundary 的目录面（frontend/src/**）未越界。详见
  evidence/review-findings.md（F1-F7 共 7 条，全部本轮处置）。
