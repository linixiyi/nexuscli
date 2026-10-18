# 测试矩阵收口:17 项零验证表面 + 45 项仅机器表面的真实使用验证 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）
- 共享只读文件：`src/nexuscli/entrypoints/repl.py`、`slash_commands.py`、`cli.py`、`tools/builtins.py`、`agent/agent.py`；lane 发现其中的缺陷一律记入 lane 反馈，不得直接改

## 阶段一：六条 lane 并行验证（第一轮工作流执行）
- [x] L1 Lane A 会话与快照：/save /resume /fork /snapshot /restore ⬜ 补机器验证+真实运行；8.4 8.5 补真实运行
  - boundary: src/nexuscli/entrypoints/commands/session_cmds.py、snapshot_cmds.py、src/nexuscli/snapshot/、session/、vfs/；新测试 tests/test_lane_a_*.py
  - verify: `.venv/bin/python -m pytest -q tests/test_lane_a_session_surface.py tests/test_lane_a_snapshot_restore.py`
- [x] L2 Lane B 记忆与索引：/memory /index /search ⬜ 补机器验证+真实运行；7.1-7.4 7.6 save/search_memory 补真实运行
  - boundary: src/nexuscli/memory/、rag/、context/、entrypoints/commands/memory_cmds.py；新测试 tests/test_lane_b_*.py
  - verify: `.venv/bin/python -m pytest -q tests/test_lane_b_memory_surface.py tests/test_lane_b_index_search.py`
- [x] L3 Lane C 模式与子代理：/team(REPL 内 + worker-mode=plan ⬜) /task ⬜ task 工具；2.8 2.9 2.10 /workflow 12.6 12.7 12.8 补真实运行
  - boundary: src/nexuscli/plan/、multi_agent/、agent/（除 agent.py）、workflow/、entrypoints/commands/task_cmds.py、workflow_cmds.py；新测试 tests/test_lane_c_*.py
  - verify: `.venv/bin/python -m pytest -q tests/test_lane_c_team_task.py tests/test_lane_c_modes.py`
- [x] L4 Lane D 技能/插件/扩展：12.1-12.5 6.8 6.9 /init /expert /goal load_skill/save_skill 补真实运行，缺断言的补断言
  - boundary: src/nexuscli/skill/、plugins/、hooks/、builtin_skills/、entrypoints/commands/custom_cmds.py、goal.py、skill.py；新测试 tests/test_lane_d_*.py
  - verify: `.venv/bin/python -m pytest -q tests/test_lane_d_extensions.py`
- [x] L5 Lane E 工具面/MCP/SDK：directory_tree get_file_info search_code todo_write/read /hitl /model /mcp ⬜ 补机器验证+真实运行；edit_file revert_turn 10.2 10.3 11.9 补真实运行
  - boundary: src/nexuscli/tools/file_ops.py、todo_store.py、entrypoints/model_selector.py、commands/model_cmds.py、permission.py、src/nexuscli/mcp/、sdk.py；新测试 tests/test_lane_e_*.py
  - verify: `.venv/bin/python -m pytest -q tests/test_lane_e_tool_surfaces.py tests/test_lane_e_mcp_sdk.py`
- [x] L6 Lane F 可观测性与媒体：13.1-13.4 14.1-14.5 补真实运行
  - boundary: src/nexuscli/observability.py、telemetry/、llm/（io dump 部分）、image/、lsp/；新测试 tests/test_lane_f_*.py
  - verify: `.venv/bin/python -m pytest -q tests/test_lane_f_observability.py tests/test_lane_f_media.py`

## 阶段二：轮间收口（主线程，依据第一轮反馈）
- [x] C1 处置第一轮跨 lane 反馈：共享文件缺陷由主线程修复并补回归测试
  - boundary: repl.py、slash_commands.py、cli.py、tools/builtins.py、agent/agent.py 中反馈点及配套测试
  - verify: `.venv/bin/python -m pytest -q`（收口点对应测试文件，主线程按反馈确定）
- [x] C2 第二轮工作流：以上轮反馈为输入，复验失败项、验证 C1 修复、清扫剩余
  - boundary: 同 L1-L6 相应边界，仅触碰第一轮标记的表面
  - verify: `.venv/bin/python -m pytest -q`

## 阶段三：验收
- [x] E1 测试矩阵文档 62 项状态列按证据如实更新（✅/🟡/⬜ → 有据可查）
  - boundary: docs/testing-matrix.md
  - verify: manual: 主线程对照 evidence/lane-*.md 逐条改状态标记
- [x] E2 全量质量门：全量 pytest + ruff
  - boundary: 只跑验证，不改代码
  - verify: `.venv/bin/python -m pytest -q` 与 `.venv/bin/python -m ruff check src tests`

---

**当前进度**：由脚本计算，无需手动维护
