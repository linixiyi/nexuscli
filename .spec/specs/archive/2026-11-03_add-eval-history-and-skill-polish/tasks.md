# eval 报告历史趋势（轻量）+ rule-demo 演示技能收窄 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] rule-demo description 收窄 + 候选匹配回归测试（一般技术查询不命中、显式点名命中；
  场景 03 链路测试不回归）
  - boundary: 只改 .agents/skills/rule-demo/SKILL.md 的 frontmatter description 与 tags、
    tests 新增；不改 SKILL.md 正文规则与场景 03
  - verify: `uv run --extra dev python -m pytest -q -k "skill or eval"`
- [x] eval history：两轨 run 成功后追加时间戳快照到 .nexuscli/evals/history/{hermetic,real}/
  （写失败降级一次性警告）+ `eval history [N]` 子命令列示 + 测试
  - depends-on: task_0001
  - boundary: 只改 src/nexuscli/eval/reporter.py、src/nexuscli/eval/real.py（history 追加）、
    src/nexuscli/entrypoints/cli.py（子命令）、tests 新增；三份既有报告/对比文件的路径与
    schema 零改动
  - verify: `uv run --extra dev python -m pytest -q -k "eval or skill"`
- [x] 文档同步与全量回归（docs/eval.md history 小节、docs/repl-commands.md 子命令表、
  README 评测回归行）
  - depends-on: task_0002
  - boundary: 只改 docs/eval.md、docs/repl-commands.md、README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`

---

**当前进度**：由脚本计算，无需手动维护
