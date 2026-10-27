# eval 当 probe 的 skill 自进化最小闭环（提案+人审） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 提案生成 + 人审落地 + 提案记录（skill/propose.py、/skill propose 与 /skill proposals
  子命令、三种空转提示、假 LLM 注入测试、-p 失败关闭）
  - boundary: 只改 src/nexuscli/skill/（新增 propose.py，evolve.py 如需只加不改既有语义）、
    src/nexuscli/entrypoints/commands/skill.py、repl.py 的 /skill 帮助文案、tests 新增；
    eval 模块只读报告文件不改代码；不动 agent 核心与 save_skill 工具
  - verify: `uv run --extra dev python -m pytest -q -k skill`
- [x] 文档同步与全量回归
  - depends-on: task_0001
  - boundary: 只改 docs/skills.md、docs/repl-commands.md、README Skill 行；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`

---

**当前进度**：由脚本计算，无需手动维护
