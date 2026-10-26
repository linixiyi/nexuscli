# 夜间①功能全量验证基线 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：验证执行
- [x] task_0001 后端全量回归
  - boundary: 只读执行 pytest 全量，不改任何文件；失败用例逐条记录进 findings.md，不现场修
  - verify: `uv run --extra dev python -m pytest -q; printf '\nPYTEST_EXIT=%s\n' "$?"`（运行目录 /Users/linyixi/nexuscli；成功判定：输出完整统计行与 PYTEST_EXIT 真值；有失败用例不算本任务失败，逐条入 findings）
- [x] task_0002 静态检查与构建
  - boundary: 只读执行 ruff check / ruff format --check / uv build
  - verify: `uv run --extra dev ruff check .; printf 'RUFF_EXIT=%s\n' "$?"; uv run --extra dev ruff format --check .; printf 'FMT_EXIT=%s\n' "$?"; uv build; printf 'BUILD_EXIT=%s\n' "$?"`（运行目录 /Users/linyixi/nexuscli；成功判定：三个 EXIT 真值齐全；非零即 finding）
- [x] task_0003 CLI 冒烟
  - boundary: 只执行 --version 与 doctor，不进入交互
  - verify: `uv run nexuscli --version; printf 'VER_EXIT=%s\n' "$?"; uv run nexuscli doctor --cwd .; printf 'DOCTOR_EXIT=%s\n' "$?"`（运行目录 /Users/linyixi/nexuscli；成功判定：两个 EXIT 真值齐全且版本号合法）
- [x] task_0004 前端构建与 e2e
  - boundary: 只在 frontend/ 下执行 npm run build 与 npm run test:e2e，不改前端代码
  - verify: `cd frontend && npm run build; printf 'FE_BUILD_EXIT=%s\n' "$?"; npm run test:e2e; printf 'E2E_EXIT=%s\n' "$?"`（运行目录 /Users/linyixi/nexuscli；成功判定：两个 EXIT 真值齐全；环境缺失记外部阻塞）
- [x] task_0005 核心模块逐项核查，产出 findings.md
  - depends-on: task_0001, task_0002, task_0003, task_0004
  - boundary: 只读核查 agent(三路径)、tools、policy、memory、runtime、mcp、render、context 八个模块与 NEXUS.md 修改联动规则、AGENTS.md 守卫的符合度；结论只写入包内 findings.md
  - verify: `test -s .spec/specs/2026-10-27_night-verify-baseline/findings.md && rg -q 'P0|P1|P2' .spec/specs/2026-10-27_night-verify-baseline/findings.md`（运行目录 /Users/linyixi/nexuscli；成功判定：findings.md 非空且含分级标记；无缺陷也要有「验证通过项汇总」与遗留清单）

---

**当前进度**：由脚本计算，无需手动维护
