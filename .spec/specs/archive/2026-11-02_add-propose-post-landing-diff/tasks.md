# propose 落地后自动 real-eval 对比（闭环后半环） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 落地后 diff 四步函数 + propose 尾段接线（备份→跑→对比→还原；二次确认 Reason 行；
  非 TTY/无 Key/执行异常三类退化；post_landing 字段；假 LLM + 注入假 runner 的全路径测试）
  - boundary: 只改 src/nexuscli/skill/propose.py（新增函数不改既有签名）、
    src/nexuscli/entrypoints/commands/skill.py 人审尾段、tests 新增；eval/real.py 只调用
    不修改
  - verify: `uv run --extra dev python -m pytest -q -k skill`
- [x] 文档同步与全量回归（docs/skills.md 闭环后半环节、README Skill 行）
  - depends-on: task_0001
  - boundary: 只改 docs/skills.md 与 README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真实链路验收（主会话经 spec_verify：真网关跑一次四步函数）
  - depends-on: task_0002
  - boundary: 只运行验证脚本与读写临时提案记录，不产生代码改动
  - verify: `uv run python -c "import json,pathlib,nexuscli.skill.propose as P; rec={'name':'rule-demo','applied_versions':[1]}; out=P.run_post_landing_diff(rec, cwd='.', runner=None, confirm=lambda reason: True, interactive=True); pl=out.get('post_landing') or {}; assert pl.get('after',{}).get('status') in ('pass','fail'), out; restored=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); assert restored['summary']['scenarios_total']==pl['before']['scenarios_total'], (restored['summary'], pl['before']); print('POST-LANDING-OK after=', pl['after']['status'], '| restored=', pl.get('report_restored', True))"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线；由 spec_verify 从主会话
      执行（AGENTS.md「执行环境与冒烟」守则）；confirm 注入 True、interactive=True
      显式声明主会话语境（spec_verify 的 stdin 非 TTY，缺省会走 non-interactive 跳过）
    - 成功判定：打印 POST-LANDING-OK——真实跑了场景、post_landing 落档、且
      last-real-report.json 还原到与调用前一致（还原不变量 = 场景数与调用前相等，
      不假设基线形态：基线可能是全量报告也可能是此前 --file 单场景报告）
    - 失败处理：断言失败先查 post_landing 错误字段与报告是否被污染；还原不变量破坏
      （场景数与调用前不一致）说明还原逻辑有缺陷，修复后再重验；历史教训两条——
      初版 verify `;` 后接 `from import` 语法错误；第二版 `scenarios_total>=2` 前提
      过期（包 A 验收已把基线覆写为单场景报告）。均为 verify 自身错误，非实现缺陷

---

**当前进度**：由脚本计算，无需手动维护
