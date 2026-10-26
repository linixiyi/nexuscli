# 夜间④Workbench UI 美化 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] task_0001 设计语言成文（impeccable document 流程）
  - boundary: 只新增根目录 DESIGN.md，不改任何代码
  - verify: `test -s DESIGN.md && rg -q "impeccable:design-schema" DESIGN.md && rg -q "Behavior pins" DESIGN.md`（运行目录 /Users/linyixi/nexuscli）
- [x] task_0002 浏览器表面 CSS 批修
  - boundary: 只在 frontend/src/index.css 追加 browser-surfaces 段（::selection/caret/tabular-nums/underline-offset），不改既有规则
  - verify: `cd frontend && npm run build; printf 'BUILD=%s\n' "$?"`（运行目录 /Users/linyixi/nexuscli；成功判定 BUILD=0）
- [x] task_0003 行为锚回归
  - boundary: 只读执行 e2e 全套与 web/runtime pytest
  - verify: `cd frontend && npm run test:e2e; printf 'E2E=%s\n' "$?"; cd .. && uv run --extra dev python -m pytest tests/test_runtime.py tests/test_runtime_websession.py tests/test_web_fetch_policy.py -q; printf 'WEBPY=%s\n' "$?"`（成功判定：E2E=0 且 WEBPY=0）
- [x] task_0004 真服务截图巡检一轮（impeccable bounded passes）
  - boundary: 一次性 node playwright 脚本（不入库），截图 5 张（桌面/任务/用量/门/移动门），结论写入包内 visual-review.md
  - verify: `test -s .spec/specs/2026-10-27_workbench-ui-polish/visual-review.md && rg -q "巡检结论" .spec/specs/2026-10-27_workbench-ui-polish/visual-review.md`（运行目录 /Users/linyixi/nexuscli）

---

**当前进度**：由脚本计算，无需手动维护
