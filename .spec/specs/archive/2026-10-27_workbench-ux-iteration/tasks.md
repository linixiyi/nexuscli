# Workbench UX 实测迭代 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）
- 多会话/多 agent 共享时，任务可选加一行 `claim: <认领者> <ISO 时间>` 声明在做者；纯信息约定，非锁、无机器键、不进门禁，勾选即失效，归档保留

## 阶段一：实现

- [x] task_0001 前端四修（U1/U3/U4/U5）+ U2 前端半边
  - boundary: 只改 frontend/src/{useWorkbench.ts,App.tsx,views/ChatPanel.tsx,
    views/ApprovalCard.tsx}；U7 的 e2e 断言同步与 journey 脚本入库
    （scripts/ui_journey.mjs）也在此任务
  - verify: `cd frontend && npm run build && npx playwright test e2e/workbench.spec.ts`（成功判定：
    build 零错误、既有 e2e 全绿 + 新增 U1（坏口令错误呈现可重试）与 U2（刷新后发送
    按钮可用）回归用例绿；失败处理：先看 e2e trace）
- [x] task_0002 U2 服务端半边：reissue-csrf 端点
  - boundary: 只改 src/nexuscli/runtime/websession.py（reissue 方法）与 api.py
    （POST /v1/workbench/reissue-csrf 路由：无有效 cookie 401、有效则重签并返回
    新 csrf）+ 对应测试（tests/test_runtime_hardening.py 或新文件）；bootstrap
    一次性语义与既有端点不动
  - verify: `uv run python -m pytest tests -q -k "runtime or bootstrap"`（成功判定：
    exit 0 且新增用例覆盖：无 cookie 401、有 cookie 重签成功且新 csrf 可过 CSRF 门、
    重签后旧 csrf 失效；失败处理：查 websession 单值替换语义）
- [x] task_0003 U7 文案用户化 + 文档同步
  - depends-on: task_0001, task_0002
  - boundary: 只改 frontend/src/views/{TasksView,UsageView}.tsx 副标题文案、
    docs/workbench.md 与 docs/runtime-api.md 鉴权小节（reissue-csrf 说明）
  - verify: `uv run python -m pytest tests -q -k docs_integrity && cd frontend && npx playwright test e2e/workbench.spec.ts`（成功判定：exit 0 且 e2e（已按 U7 新文案
    断言）绿；失败处理：查断言同步遗漏）

## 阶段二：对照验收

- [x] task_0004 journey 重截图对照与全量门
  - depends-on: task_0001, task_0002, task_0003
  - boundary: 不改业务代码；复跑 journey（入库的 scripts/ui_journey.mjs）产出
    evidence/after/，逐张对照 findings.md；只允许按门禁结果回修各任务 boundary 文件
  - verify: `uv run python -m pytest tests -q && uv run ruff check . && uv run ruff format --check . && python3 scripts/ui_journey.py --out .spec/specs/2026-10-27_workbench-ux-iteration/evidence/after`（前置：仓库根；成功判定：前两段 exit 0 且
    after/ 截图齐全，02 对应位置呈现错误可重试、11 对应位置发送按钮可用；失败处理：
    按 findings.md 对照表定位）

---

**当前进度**：由脚本计算，无需手动维护