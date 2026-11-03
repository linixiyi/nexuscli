# rag 目录限定检索：OpenViking expr.py 搬运 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] expr 搬运 + scope 检索（clone 上游 @ 9a57854 只读参考；rag 新增 expr/路径限定模块
  并合规注记；search/search_with_stats 加可选 scope；search_code 工具透传；测试四类：
  子树内命中/子树外排除/嵌套 scope/None 逐字节兼容 + 分隔符归一 + 工具透传）
  - boundary: 只改 src/nexuscli/rag/**、src/nexuscli/tools/builtins.py 的 search_code、
    tests 新增、docs/tools.md；不改索引文件格式与 memory/eval
  - verify: `uv run --extra dev python -m pytest -q -k "rag"`
- [x] 文档同步与全量回归（docs/tools.md 检索节补 scope 用法；README 检索相关行核对）
  - depends-on: task_0001
  - boundary: 只改 docs/tools.md 与 README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`

---

**当前进度**：由脚本计算，无需手动维护
