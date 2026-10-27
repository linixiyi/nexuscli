# 可选 OTLP 导出端点（Langfuse 等外挂观测） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] OTLP 导出与降级路径（telemetry.otlp 配置三键、exporter 惰性导入进 extras、
  假 OTLP HTTP 接收端测试、缺依赖与不可达两条降级、默认关闭零感知）
  - boundary: 只改 src/nexuscli/telemetry/、src/nexuscli/config.py 的 telemetry 配置
    面、pyproject.toml extras、tests 新增；不改既有 span 语义与 llm/agent
  - verify: `uv run --extra dev python -m pytest -q -k telemetry`
- [x] 文档同步与全量回归
  - depends-on: task_0001
  - boundary: 只改 docs/observability.md、docs/config.md、README 可观测性行；不改
    src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`

---

**当前进度**：由脚本计算，无需手动维护
