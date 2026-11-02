# 套件稳定性基线：repeat=3 pass^k 全量真网关测定 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] repeat=3 全量真网关测定（主会话后台执行，90 次运行）
  - boundary: 只运行真实入口与读取报告，不产生代码改动
  - verify: `uv run python -c "import json,pathlib; rep=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); scs=rep['scenarios']; assert len(scs)==30, len(scs); assert all(s['pass_k']['repeat']==3 for s in scs), [s['pass_k'] for s in scs[:3]]; assert rep['summary'].get('all_pass_rate') is not None, rep['summary']; print('STABILITY-RUN-OK all_pass_rate=', rep['summary']['all_pass_rate'])"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线；双跑本体
      `uv run nexuscli eval run --real --file examples/evals/suite --repeat 3` 由主会话
      后台执行（AGENTS.md「执行环境与冒烟」守则 + spec_verify 600s 上限）
    - 成功判定：打印 STABILITY-RUN-OK 与 pass^3 全通过率
    - 失败处理：场景数≠30 或 repeat≠3 说明 run 未完成或被中途打断——查
      /tmp/stability-run.log 尾部，重跑
- [x] manifest 回填 stability + docs 方差量化 + 全量回归
  - depends-on: task_0001
  - boundary: 只改 examples/evals/suite/meta/manifest.json（新增 stability 字段，不动
    calibration）、docs/eval.md（方差量化段：三次采样对比 + 摇摆题清单）、README 评测
    回归行；不改 src 与场景题面
  - verify: `uv run python -c "import json,pathlib; man=json.loads(pathlib.Path('examples/evals/suite/meta/manifest.json').read_text()); miss=[e['id'] for e in man['scenarios'] if not (e.get('stability') or {}).get('repeat')==3]; assert not miss, miss;sway=[e['id'] for e in man['scenarios'] if 0<e['stability']['passed_runs']<3]; print('BACKFILL-OK sway=', len(sway))" && uv run --extra dev python -m pytest -q tests/test_eval_suite.py && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
    - 运行目录：仓库根；成功判定：打印 BACKFILL-OK 与摇摆题数；套件一致性测试、
      ruff 双检全绿
    - 失败处理：miss 非空说明回填漏题；sway 为摇摆题 id 列表

---

**当前进度**：由脚本计算，无需手动维护
