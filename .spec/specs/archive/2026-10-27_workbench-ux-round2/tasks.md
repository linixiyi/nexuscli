# Workbench UX 第二轮迭代 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）
- 多会话/多 agent 共享时，任务可选加一行 `claim: <认领者> <ISO 时间>` 声明在做者；纯信息约定，非锁、无机器键、不进门禁，勾选即失效，归档保留

## 阶段一：实现

- [x] task_0001 三项前端修（W1/W2/U6）+ e2e 断言
  - boundary: 只改 frontend/src/{views/ChatPanel.tsx,views/Sidebar.tsx,
    views/Inspector.tsx} 与 frontend/e2e/workbench.spec.ts（新增断言）
  - verify: `cd frontend && npm run build && npx playwright test e2e/workbench.spec.ts`（成功判定：
    build 零错误、e2e 全绿且新断言（慢等待提示以可控阈值触发、回退标题、逐条
    证据列表）生效；失败处理：先看 e2e trace 与计时器条件）
- [x] task_0002 journey 回归与文档一句
  - depends-on: task_0001
  - boundary: 复跑 journey（/tmp/ux2-after）+ docs/workbench.md 功能面一句
    （慢等待提示与会话列表辨识）
  - verify: `uv run python scripts/ui_journey.py --out /tmp/ux2-after && uv run python -m pytest tests -q -k docs_integrity && uv run ruff check . && uv run ruff format --check .`（成功判定：
    全部 exit 0 且 journey 零页错误；失败处理：按段定位）

---

**当前进度**：由脚本计算，无需手动维护