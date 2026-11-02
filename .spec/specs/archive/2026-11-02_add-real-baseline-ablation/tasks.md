# real 轨 baseline 消融：agent vs 裸模型 harness 增益报告 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 裸模型执行路径 + --baseline 双跑与对比装配（run_baseline_scenario 单补全 +
  final_contains/judge 子集断言；run_baseline_comparison 双跑 + per-scenario delta +
  四象限 + 汇总双列；last-real-comparison.json 独立落盘；CLI 接线；全路径 hermetic 测试）
  - boundary: 只改 src/nexuscli/eval/real.py（新增路径与常量）、
    src/nexuscli/entrypoints/cli.py eval 命令组（--baseline）、tests 新增；
    last-real-report.json schema 零改动、hermetic 轨零改动、场景文件零改动
  - verify: `uv run --extra dev python -m pytest -q -k eval`
- [x] 文档同步与全量回归（docs/eval.md --baseline 口径与对比文件字段表、README 评测回归行）
  - depends-on: task_0001
  - boundary: 只改 docs/eval.md 与 README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真网关 30 题双跑验收（主会话执行双跑本体，verify 断言对比文件）
  - depends-on: task_0002
  - boundary: 只运行真实入口与读取报告，不产生代码改动
  - verify: `uv run python -c "import json,pathlib; cmp=json.loads(pathlib.Path('.nexuscli/evals/last-real-comparison.json').read_text()); s=cmp['summary']; assert s.get('agent_pass') is not None and s.get('baseline_pass') is not None, s; q=s.get('quadrants') or {}; assert set(q)>= {'both_pass','agent_only','baseline_only','both_fail'}, q; rep=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); assert rep['summary']['scenarios_total']==30, rep['summary']; print('ABLATION-OK agent', s['agent_pass'], 'baseline', s['baseline_pass'], '| quadrants', q)"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线；双跑本体
      `uv run nexuscli eval run --real --baseline --file examples/evals/suite` 由主会话
      后台执行（AGENTS.md「执行环境与冒烟」守则 + spec_verify 600 秒上限，同包 C 处置），
      完成后本 verify 断言对比文件与主报告
    - 成功判定：打印 ABLATION-OK——双 pass 率、四象限齐备；主报告仍是 agent 侧 30 题
      全量形态（schema 未变）
    - 失败处理：对比文件缺失说明双跑未完成（重跑双跑）；四象限缺键看装配代码；主报告
      scenarios_total≠30 说明双跑覆写了主报告（真缺陷，修隔离后重验）

---

**当前进度**：由脚本计算，无需手动维护
