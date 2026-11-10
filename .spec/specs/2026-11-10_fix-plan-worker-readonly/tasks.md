# Plan 模式 worker 只读约束 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在仓库根目录可执行。
- 任务 0001 先做代码与运行时约束，任务 0002 再做文档与文档契约测试。
- 不改 `src/nexuscli/tools/executor.py` 的只读闸；执行层 blocked 继续作为安全兜底。
- 不引入新依赖；不修改公共 API 签名。

## 阶段 A：worker 运行时只读约束

- [ ] task-0001 plan 模式 worker 只暴露六个只读工具
  - id: task-0001
  - boundary: src/nexuscli/agent/plan_execute.py, new:tests/test_plan_worker_tools.py
  - origin: planned
  - expect: red
  - behavior: 在 `plan_execute.py` 增加 plan 模式 worker 工具白名单（`read_file`、`glob`、`grep`、`search_code`、`vfs_ls`、`vfs_read`）与 registry 过滤 helper；`_execute_task_stream()` 传给 `query()` 的 registry 在 plan 模式下必须是过滤后的子集，非 plan 模式保持完整 registry；`_task_system_prompt()` 使用过滤后的工具名，并明确写“当前是 plan 只读模式，只能使用上述只读工具，不要调用 bash/execute_command/write_file/edit_file；任务描述要求只读时不要自行改成写入或命令”。不改变 executor 的只读闸与 blocked 语义。
  - tests: 新增 `tests/test_plan_worker_tools.py`，至少覆盖：① plan 模式过滤后只含六个工具，bash/write_file/edit_file 不在列表中；② 非 plan 模式返回完整 registry；③ `_task_system_prompt()` 包含 plan 只读约束与禁止写工具字样；④ 若可低成本构造，断言 `_execute_task_stream()` 调用 `query()` 时传入的是过滤后的 registry（monkeypatch `query` 捕获参数）。
  - verify: `uv run pytest -q tests/test_plan_worker_tools.py`

- [ ] task-0002 文档同步 worker 只读语义
  - id: task-0002
  - boundary: README.md, docs/plan-execute.md, new:tests/test_plan_worker_docs.py
  - depends-on: task-0001
  - origin: planned
  - expect: red
  - behavior: 更新 README 的 `--mode plan` 说明与 `docs/plan-execute.md` 的只读规划段落，明确：planner 指令白名单与 worker 运行时 registry 过滤是双重约束；worker 只看到六个只读工具；执行层只读闸仍会拒绝任何漏网的写入/命令并 blocked。措辞不得声称 plan 模式会执行写入，也不得把 `--yes` 描述成可绕过只读闸。
  - tests: 新增 `tests/test_plan_worker_docs.py`，断言 README 与 docs/plan-execute.md 均包含 worker 运行时只读、六个工具、bash/write_file 禁止、执行层 blocked 兜底等关键词；不依赖网络与真实模型。
  - verify: `uv run pytest -q tests/test_plan_worker_docs.py`

---

**当前进度**：由脚本计算，无需手动维护
