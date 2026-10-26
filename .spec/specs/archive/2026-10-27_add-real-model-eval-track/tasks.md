# 真模型 eval 轨道：pass^k + 真实成本 + LLM-as-judge - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 真模型轨道执行路径与 CLI 接线（--real/--repeat、无 Key 明确报错、只读工具面、
  失败关闭权限、usage 采集、假 LLM 可注入测试）
  - boundary: 只改 src/nexuscli/eval/（新增 real 轨道执行路径与配置变体）、
    src/nexuscli/entrypoints/ 的 eval 命令组（加旗标）、tests 新增；不改 hermetic
    轨道既有行为与报告格式，不改 agent/memory/telemetry
  - verify: `uv run --extra dev python -m pytest -q -k eval`
- [x] pass^k 聚合 + 真实成本记录 + judge 断言（rubric/阈值/评审模型覆盖/假 judge 注入）
  + last-real-report.json 报告格式 + 内置 real 示例场景 ≥2 个
  - depends-on: task_0001
  - boundary: 只改 src/nexuscli/eval/、examples/evals/real/、tests 新增；报告文件独立
    于 hermetic 基线
  - verify: `uv run --extra dev python -m pytest -q -k eval`
- [x] 文档同步与全量回归（docs/eval.md 两轨道口径重写、README 评测回归行）
  - depends-on: task_0002
  - boundary: 只改 docs/eval.md 与 README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真模型机会性冒烟（最小场景 1 轮，留成本证据）
  - depends-on: task_0003
  - boundary: 只运行真实入口，不产生代码改动；无可用 Key 或网络不通时按失败形态处置
  - verify: manual: 真模型冒烟依赖真实 API Key 与网络且产生真实计费，本环境可用性
    运行时才可知，不作为门禁——轨道逻辑已由假 LLM 注入测试全量覆盖；执行时若
    Key 可用则跑 `uv run nexuscli eval run --real --repeat 1` 留证据备注，不可用
    则记录外部受阻，不伪造通过

---

**当前进度**：由脚本计算，无需手动维护
