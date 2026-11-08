# NexusCLI 发布链与执行安全加固 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在仓库根目录可执行。
- 任务按阶段 A → B → C 执行；同阶段内按编号顺序执行，`depends-on` 是硬前置。
- 每个任务完成后勾选，并在任务下手写一行 `evidence: <命令> -> exit <code>, <关键输出>`；不要写“已验证”但没有输出。
- 若 verify 失败，先修当前任务；修不动就把失败命令、退出码、前 30 行输出写回本任务，不要跳到下一个任务。
- manual 任务必须由 owner 执行，并记录执行人、命令、结果；不能由 AI 代填。
- 包内不使用日历时间；阶段只表达依赖顺序。

## 阶段 A：发布阻断与安全止血

（0006/0008/0009 三项 owner 手动任务已逐字转入后续包 2026-11-08_add-publish-demo-protection，见 spec.md 5.6 偏离日志 D3）

- [x] 0001 修复仓库 lint/format 阻断，恢复 CI 绿
  - id: task-0001
  - boundary: 只改 `site/build.py`（`29:70` 与 `45:72` 两处 E501，以及 `ruff format` 要求的重排）；不重排其他文件，不改站点生成内容。
  - verify: `uv run ruff check . && uv run ruff format --check .`
  - evidence: uv run ruff check . && uv run ruff format --check . -> exit 0, 修复前复现 2 处 E501（site/build.py:29:70 与 45:72，102>100）且 format 仅欲重排 site/build.py；按任务期望执行 uv run ruff format site/build.py 后复跑 verify：ruff check . 输出 'All checks passed!'，ruff format --check . 输出 '290 files already formatted'，两条均 exit 0；git status 仅 M site/build.py，diff 确认只有 DOC_META 两条目按 formatter 拆为多行元组，字符串内容未变（站点生成内容不变）
  - 期望：两条命令均 exit 0；若 `ruff format` 改动文件，先执行 `uv run ruff format site/build.py` 再复跑。若出现 `site/build.py` 之外的 lint 错误，停止并记录，不要扩大 boundary。

- [x] 0002 将 slash `!` 注入接入 ToolExecutor 审批、审计与环境清洗
  - id: task-0002
  - depends-on: task-0001
  - boundary: `src/nexuscli/entrypoints/slash_commands.py`、`src/nexuscli/tools/commands.py`、`src/nexuscli/tools/executor.py`（仅当需要内部适配器时）、`tests/test_slash_commands.py`、新增 `tests/test_slash_injection_security.py`、`docs/slash-commands.md`。
  - 行为：`_run_injection` 不再直接调用 `CommandExecutor.execute`；改为通过 `ToolExecutor` 执行（可新增内部 `slash_injection` 工具适配器，但必须复用同一条门链）。项目级命令（`CustomCommand.source == "project"`）的注入必须 `requires_approval=True`；无审批回调时 fail-closed，拒绝时保留现有 marker 语义且不执行命令。用户级命令（`source == "user"`）可 `requires_approval=False` 自动执行，但仍必须经过审计记录与 `sanitize_environment` 环境清洗。保留 `CommandGuard` 校验、超时和错误 marker；不得把原始 `os.environ` 传给子进程。
  - 测试：项目级 + 无回调 → 命令未执行、返回拒绝 marker；项目级 + 批准 → 执行且审计有记录；用户级 → 自动执行且审计有记录；危险命令 → 仍被 CommandGuard 拒绝；设置 `OPENAI_API_KEY` 后子进程环境不含该键。
  - verify: `uv run pytest -q tests/test_slash_commands.py tests/test_slash_injection_security.py`
  - evidence: uv run pytest -q tests/test_slash_commands.py tests/test_slash_injection_security.py -> exit 0, 26 passed (existing 18 + new 8 security tests), exit 0; ruff format/check clean on changed .py files

- [x] 0003 让 MCP 的 readOnlyHint 不再产生本地可信只读
  - id: task-0003
  - depends-on: task-0001
  - boundary: `src/nexuscli/mcp/client.py`、新增 `tests/test_mcp_security.py`、`docs/mcp.md`。
  - 行为：构造 MCP `Tool` 时忽略 `annotations.readOnlyHint` 的安全含义；统一 `is_read_only=False`、`requires_approval=True`、`danger_level="medium"`（或项目既有非 safe 等级）。plan 模式不再直接放行 MCP 工具；auto/default 模式仍按审批链询问。`readOnlyHint` 可用于展示/描述，但不得进入 `is_read_only` 或 `requires_approval` 计算。
  - 测试：fake server 返回 `readOnlyHint=True`；断言 `Tool.is_read_only is False`、`requires_approval is True`；plan 模式执行被拒；auto 模式触发审批回调；`readOnlyHint=False` 行为不变。
  - verify: `uv run pytest -q tests/test_mcp.py tests/test_mcp_filter.py tests/test_mcp_security.py`
  - evidence: uv run pytest -q tests/test_mcp.py tests/test_mcp_filter.py tests/test_mcp_security.py -> exit 0, 20 passed in 0.67s（exit 0；含新增 7 个用例：hint 真假均 is_read_only=False/requires_approval=True/danger=medium、hint 仅进 description、plan 模式拒绝、auto 模式触发审批回调且 deny 不执行、批准后经同一门链执行；uv run ruff format + check 对两个 .py 文件均通过）

- [x] 0004 让 wheel 自带 workbench，sdist 排除 node_modules 与 .spec
  - id: task-0004
  - depends-on: task-0001
  - boundary: `pyproject.toml`、`src/nexuscli/runtime/api.py`、新增 `scripts/check_release_artifacts.py`、新增 `tests/test_workbench_static.py`、`.gitignore`、从 git index 移除 `frontend/test-results/.last-run.json`（保留本地文件）。
  - 行为：hatchling wheel 用 force-include 把 `frontend/dist` 打到 `nexuscli/frontend/dist`；sdist 排除 `frontend/node_modules/**`、`frontend/test-results/**`、`.spec/**`；`_workbench_dist_root()` 在 cwd 不存在 dist 时回落到包内 `nexuscli/frontend/dist`；`NEXUSCLI_WORKBENCH_DIST` 仍最高优先级。`scripts/check_release_artifacts.py` 用 `uv build` 到临时目录，检查 wheel 含 `nexuscli/frontend/dist/index.html`、sdist 不含 `node_modules` 与 `.spec`，失败时打印缺失/多余路径。
  - 测试：`tests/test_workbench_static.py` 用临时 cwd 断言包内回落；保留 env 覆盖测试。
  - verify: `uv run python scripts/check_release_artifacts.py && uv run pytest -q tests/test_workbench_static.py`
  - evidence: uv run python scripts/check_release_artifacts.py && uv run pytest -q tests/test_workbench_static.py -> exit 0, check_release_artifacts: OK wheel=nexuscli-0.2.0-py3-none-any.whl sdist=nexuscli-0.2.0.tar.gz; pytest 6 passed; 改前基线实测 sdist 泄漏 node_modules 3863 条/.spec 603 条/test-results 1 条、wheel 0 个 frontend 条目，改后 sdist 三项均为 0 且 wheel 含 nexuscli/frontend/dist/index.html+assets；解包新 wheel 到独立目录后 _packaged_workbench_dist() 与裸 cwd 的 _workbench_dist_root() 均解析到包内 dist 且 index.html 存在；回归 uv run python -m pytest -q tests/test_runtime_static.py 7 passed

- [x] 0005 增加 tag 触发的 PyPI release workflow 与 0.2.1 版本
  - id: task-0005
  - depends-on: task-0004
  - boundary: 新增 `.github/workflows/release.yml`、`pyproject.toml`、`src/nexuscli/__init__.py`、新增 `CHANGELOG.md`、新增 `tests/test_release_workflow.py`。
  - 行为：版本统一升到 `0.2.1`；workflow 在 `push.tags: ["v*"]` 触发，`permissions.id-token: write`，`environment: pypi`，用 `uv build` 产出 wheel/sdist，`python -m twine check dist/*`，用 pinned 的 `pypa/gh-action-pypi-publish` 上传；不把 token 写进仓库。测试解析 workflow 文本，断言触发、权限、environment、build、twine check、publish 步骤存在，且版本两处一致。
  - verify: `uv run pytest -q tests/test_release_workflow.py`
  - evidence: uv run pytest -q tests/test_release_workflow.py -> exit 0, 7 passed in 0.02s（tag 触发/permissions/environment/build→twine→publish 顺序/pin/无 token/版本两处一致 0.2.1）；uv run ruff format + ruff check 对改动 .py（tests/test_release_workflow.py、src/nexuscli/__init__.py）：2 files left unchanged、All checks passed!

- [x] 0007 README 安装路径、社区文件与 docs contract
  - id: task-0007
  - depends-on: task-0005
  - boundary: `README.md`、新增 `CONTRIBUTING.md`、新增 `SECURITY.md`、新增 `.github/ISSUE_TEMPLATE/bug_report.yml`、新增 `.github/ISSUE_TEMPLATE/feature_request.yml`、新增 `.github/pull_request_template.md`、新增 `tests/test_docs_contract.py`。
  - 行为：README 增加 PyPI（0.2.1 起）、`uv tool install nexuscli`、源码安装三种路径，补 60 秒 quickstart、docs 索引、开发/测试命令；CONTRIBUTING 写清 `uv sync --extra dev`、`pytest`、`ruff`、mypy；SECURITY 写漏洞报告渠道与“护栏不是沙箱”的边界；issue/PR 模板可用。`tests/test_docs_contract.py` 断言关键命令与文件存在，不校验截图。
  - verify: `uv run pytest -q tests/test_docs_contract.py`
  - evidence: uv run pytest -q tests/test_docs_contract.py -> exit 0, 10 passed；README 431 行守住 test_docs_integrity 预算 [360,440]（uv run pytest -q tests/test_docs_integrity.py -> exit 0, 5 passed）；两个 issue 模板经 uv run --with pyyaml 实解析通过（并实测抓出并修复 bug_report.yml 两处以反引号开头的非法 YAML 标量）；ruff format '1 file left unchanged'、ruff check 'All checks passed!' 均 exit 0

## 阶段 B：执行路径统一与质量门禁

- [x] 0010 让 query 与 ReAct 共享 LoopGuard 与 max_turns 撞顶提示
  - id: task-0010
  - depends-on: task-0001
  - boundary: `src/nexuscli/agent/query.py`、`src/nexuscli/agent/agent.py`、`src/nexuscli/agent/loop_guard.py`（仅当需要抽出共享 helper）、新增 `tests/test_loop_guard_parity.py`。
  - 行为：`query()` 使用与 ReAct 相同的 `LoopGuard`；把 `max_turns` 撞顶提示抽成共享 helper，两条路径都输出可读 warning；不改变现有 `max_turns` 默认值语义。测试用 fake LLM 制造重复工具调用，断言两条路径都在阈值处停止并给出同类提示。
  - verify: `uv run pytest -q tests/test_loop_guard_parity.py tests/test_agent_turns.py`
  - evidence: uv run pytest -q tests/test_loop_guard_parity.py tests/test_agent_turns.py -> exit 0, 18 passed in 0.30s (exit 0); sanity run of tests/test_loop_detection.py tests/test_plan.py tests/test_multi_agent.py tests/test_query.py also 34 passed; ruff format+check on the 4 changed files: All checks passed

- [x] 0011 让 query 与 ReAct 共享 provider 超限重试与 F12 截断保护
  - id: task-0011
  - depends-on: task-0010
  - boundary: `src/nexuscli/agent/query.py`、`src/nexuscli/agent/agent.py`、新增 `tests/test_agent_overflow_parity.py`。
  - 行为：把 ReAct 的 `compact_now` / `force_trim` 重试逻辑与 `max_tokens` 截断保护抽成共享 helper，`query()` 在 provider 返回 context overflow 或 finish_reason=length 时走同一处理；不得无限重试，保留一次压缩后重试与最终失败路径。测试用 fake provider 依次返回 overflow/length/正常响应，断言两条路径行为一致。
  - verify: `uv run pytest -q tests/test_agent_overflow_parity.py tests/test_agent_turns.py`
  - evidence: uv run pytest -q tests/test_agent_overflow_parity.py tests/test_agent_turns.py -> exit 0, 18 passed (7 new parity + 11 existing agent_turns); sanity batch tests/test_overflow_retry.py+test_loop_guard_parity.py+test_anthropic_client.py+test_query.py+test_plan.py+test_multi_agent.py -> exit 0, 48 passed; ruff format --check '3 files already formatted' and ruff check 'All checks passed!' both exit 0

- [x] 0012 plan/team worker 的 max_turns 对齐 config
  - id: task-0012
  - depends-on: task-0010
  - boundary: `src/nexuscli/agent/plan_execute.py`、`src/nexuscli/agent/orchestrator.py`、`src/nexuscli/agent/agent.py`、`src/nexuscli/config.py`、新增 `tests/test_agent_budgets.py`。
  - 行为：移除 `orchestrator.py:185` 的 hardcoded `max_turns=8` 与 `Agent._run_plan` 把 `max_task_turns` 抬到 200 的分支；plan/team worker 统一从 `config.agent.max_turns`（或新增的单一 config 字段）读取。若确需不同预算，必须在 config 中显式命名并在 docs 说明，不得散落魔法数。
  - verify: `uv run pytest -q tests/test_agent_budgets.py tests/test_plan.py tests/test_multi_agent.py`
  - evidence: uv run pytest -q tests/test_agent_budgets.py tests/test_plan.py tests/test_multi_agent.py -> exit 0, 23 passed (6 new test_agent_budgets + 9 test_plan + 8 test_multi_agent); sanity batch test_agent_turns+test_loop_guard_parity+test_subagent+test_plan_checkpoint+test_snapshot+test_hooks_lane_coverage+test_overflow_retry+test_query -> exit 0, 66 passed; ruff format 'All checks passed!' (1 test file reformatted then re-verified 23 passed) and ruff check 'All checks passed!' both exit 0

- [x] 0013 plan 每个任务接收会话历史
  - id: task-0013
  - depends-on: task-0012
  - boundary: `src/nexuscli/agent/plan_execute.py`、`src/nexuscli/agent/agent.py`、新增 `tests/test_plan_history.py`。
  - 行为：planner 接收当前会话历史；每个 plan 任务以历史快照启动，不再固定 `history=[]`；任务产生的消息按顺序追加，done 时只追加一次，避免重复。测试用两任务计划，断言第二个任务能看到第一个任务的用户/助手消息，且最终历史无重复。
  - verify: `uv run pytest -q tests/test_plan_history.py tests/test_plan.py`
  - evidence: uv run pytest -q tests/test_plan_history.py tests/test_plan.py -> exit 0, 13 passed (5 new test_plan_history + 8 test_plan); 相关回归 tests/test_plan_checkpoint.py+test_agent_budgets.py+test_snapshot.py+test_hooks_lane_coverage.py+test_multi_agent.py -> 34 passed; ruff format '1 file reformatted, 1 file left unchanged'、ruff check 'All checks passed!'

- [x] 0014 team 并发 worker 使用隔离 history
  - id: task-0014
  - depends-on: task-0012
  - boundary: `src/nexuscli/agent/orchestrator.py`、新增 `tests/test_team_history.py`。
  - 行为：每个 worker 拿到 `list(self.history)` 的独立副本；worker 完成后按任务索引确定性合并，不再在事件循环里 `self.history = list(event["messages"])` 造成 last-writer-wins；合并保留每个 worker 的增量。测试用两个延迟不同的 fake worker，断言两条增量都在、顺序稳定、无串线。
  - verify: `uv run pytest -q tests/test_team_history.py tests/test_multi_agent.py`
  - evidence: uv run pytest -q tests/test_team_history.py tests/test_multi_agent.py -> exit 0, 11 passed（tests/test_team_history.py 3 条 + tests/test_multi_agent.py 8 条）；另跑同受影响的 team 路径 7 个测试文件（test_worker_mode_pinned/test_runtime/test_hooks_lane_coverage/test_slash_commands/test_snapshot/test_agent_budgets/test_print_mode_commands）共 60 passed；ruff format --check 与 ruff check 对两个改动文件均通过

- [x] 0015 plan worker 传入 journal/run_id，恢复不重复副作用
  - id: task-0015
  - depends-on: task-0012
  - boundary: `src/nexuscli/agent/plan_execute.py`、`src/nexuscli/agent/query.py`、`tests/test_plan_checkpoint.py`。
  - 行为：plan worker 调用 `query()` 时传入 `journal=self._journal` 与 `run_id`；删除 `plan_execute.py:55-58` 的死字段或真正接线；恢复后已完成的工具调用不再重复执行。测试用带 journal 的假工具，模拟中断/恢复，断言副作用只发生一次。
  - verify: `uv run pytest -q tests/test_plan_checkpoint.py`
  - evidence: uv run pytest -q tests/test_plan_checkpoint.py -> exit 0, 12 passed (10 existing + 2 new)；变异检查：临时改为 journal=None/run_id=self._run_id 时两条新测试 FAILED（恢复重跑副作用 assert 2 == 1、plan.run_id 查 journal miss），恢复接线后 12 passed；回归 tests/test_plan.py+test_plan_history.py+test_agent_budgets.py+test_multi_agent.py+test_query.py+test_turn_checkpoint.py+test_executor_journal.py -> 43 passed；ruff format '2 files left unchanged'、ruff check 'All checks passed!' 均 exit 0

- [x] 0016 deny/skip 分支的审计受 audit_log 控制
  - id: task-0016
  - depends-on: task-0001
  - boundary: `src/nexuscli/tools/executor.py`、新增 `tests/test_audit_gating.py`。
  - 行为：`executor.py:270-285` 的 deny/skip `audit.record` 包进 `if context.config.features.audit_log`，与同文件其他 6 处一致；feature 关闭时不写审计，开启时 deny/skip 仍有记录。测试两种配置各跑一次，断言审计文件存在/不存在。
  - verify: `uv run pytest -q tests/test_audit_gating.py`
  - evidence: uv run pytest -q tests/test_audit_gating.py -> exit 0, 5 passed（feature 关 × deny/skip 无审计文件、feature 开 × deny/skip 落 (outcome,'hitl') 记录、无回调 fail-closed deny 受开关控制）；回归 tests/test_tool_approval_matrix.py test_permissions.py test_run_scope_approval.py test_hooks.py test_trace_id.py test_bash_readonly.py test_slash_injection_security.py 共 188 passed；ruff format + check 对两个改动 .py 文件均通过

- [x] 0017 MCP server 不再就地改写共享 config
  - id: task-0017
  - depends-on: task-0001
  - boundary: `src/nexuscli/mcp/server.py`、`tests/test_mcp_server_protocol.py`。
  - 行为：`mcp/server.py:86` 改为 `copy.deepcopy(config)` 或构造局部 config 后设置 `hitl_mode="never"`；`load_config()` 返回的原对象不变。测试在 server 启动后断言原 config 的 `hitl_mode` 未被改写，且 server 内部仍为 never。
  - verify: `uv run pytest -q tests/test_mcp_server_protocol.py`
  - evidence: uv run pytest -q tests/test_mcp_server_protocol.py -> exit 0, 6 passed in 0.20s（uv run pytest tests/test_mcp_server_protocol.py -v 实跑输出；回归验证：临时还原就地改写时新测试 FAILED assert seen[0].config is not shared，重新应用 deepcopy 后 6 passed；ruff format 2 files left unchanged、ruff check All checks passed）

- [x] 0018 permission prefix 规则按词边界匹配
  - id: task-0018
  - depends-on: task-0001
  - boundary: `src/nexuscli/policy/permission_rules.py`、`tests/test_permissions.py`。
  - 行为：`prefix:*` 规则不再用裸 `startswith(rule.spec[:-2])`；匹配命令 token 边界，`bash(git diff:*)` 不得放行 `git difftool`，但应放行 `git diff --stat` 与 `git diff`。测试覆盖正例/反例/带参数/带引号。
  - verify: `uv run pytest -q tests/test_permissions.py`
  - evidence: uv run pytest -q tests/test_permissions.py -> exit 0, "25 passed in 0.11s"（22 既有 + 3 新增：-k prefix_rule 单跑 3 passed；另复跑 tests/test_bash_readonly.py 亦 exit 0），ruff format "2 files left unchanged"、ruff check "All checks passed!" 均 exit 0

- [x] 0019 `-p "/命令"` 消费 frontmatter 的 mode/allowed_tools
  - id: task-0019
  - depends-on: task-0002
  - boundary: `src/nexuscli/entrypoints/cli.py`、`src/nexuscli/entrypoints/slash_commands.py`、新增 `tests/test_print_mode_commands.py`。
  - 行为：`-p "/cmd"` 不仅展开正文，还应用命令 frontmatter 的 `mode`（如 plan）与 `allowed_tools` 限制；`allowed_tools` 为空时保持默认；未知/非法值给出可读错误，不静默忽略。测试用临时项目命令，断言 plan 模式拒绝写工具、allowed_tools 只放行列出的工具。
  - verify: `uv run pytest -q tests/test_print_mode_commands.py`
  - evidence: uv run pytest -q tests/test_print_mode_commands.py -> exit 0, 14 passed; 相关套件（slash_commands/slash_injection_security/init_command/cli_single_prompt/plan_mode/plugins/cli/session_usage/repl/slash_surfaces_docs）共 111 passed；ruff format+check 对三个改动文件全部通过

- [x] 0020 把 Playwright e2e 接入 CI
  - id: task-0020
  - depends-on: task-0001
  - boundary: 新增 `frontend/playwright.config.ts`、`frontend/e2e/workbench.spec.ts`、`frontend/package.json`、`.github/workflows/ci.yml`、`scripts/web_validation_server.py`（仅当需要固定端口/健康检查）。
  - 行为：Playwright 配置 baseURL、serial、retries 0、trace on-first-retry；e2e 使用真实 Core + `scripts/web_validation_server.py`；CI frontend job 安装 Chromium 并运行 `npm run test:e2e`，失败上传 trace。浏览器缺失时不再静默 skip（CI 中必须失败；本地可 skip）。
  - Browser check: `npm --prefix frontend run test:e2e`
  - verify: `npm --prefix frontend run test:e2e`
  - evidence: npm --prefix frontend run test:e2e -> exit 0, 11 passed (7.8s)，exit 0；另验证 CI=true 健康跑 11 passed exit 0、CI=true+浏览器缺失 1 failed exit 1（硬失败）、本地+浏览器缺失 11 skipped exit 0（本地可 skip），ci.yml 经 ruby YAML.safe_load 校验通过

- [x] 0021 workbench 支持 approve_run/approve_auto、SSE 审批与可配 timeout
  - id: task-0021
  - depends-on: task-0020
  - boundary: `src/nexuscli/runtime/api.py`、`frontend/src/views/ApprovalCard.tsx`、`frontend/src/derive.ts`（仅当需要新事件类型）、`frontend/e2e/workbench.spec.ts`、`docs/workbench.md`、`tests/test_runtime_approvals.py`、`tests/test_runtime_sse.py`。
  - 行为：API 增加 `approve_run` 与 `approve_auto`（命名可按现有风格，但语义必须是“本次 run 内放行”与“切到 auto/full access”）；SSE 推送审批请求与解决事件，前端不再只靠轮询；审批 timeout 可配，超时仍 deny；ApprovalCard 提供对应按钮与原因行。测试覆盖 API 权限、SSE 事件顺序、timeout；e2e 覆盖按钮可见与点击后的状态变化。
  - Browser check: `npm --prefix frontend run test:e2e`
  - verify: `npm --prefix frontend run test:e2e && uv run pytest -q tests/test_runtime_approvals.py tests/test_runtime_sse.py`
  - evidence: npm --prefix frontend run test:e2e && uv run pytest -q tests/test_runtime_approvals.py tests/test_runtime_sse.py -> exit 0, e2e 12 passed（含新增用例「0021 审批卡片：run/auto 按钮与 Reason 行可见，本轮放行后卡片收口且 turn 完成」）；pytest 34 passed（approvals 20 + sse 14）；ruff format/check 3 个改动 .py 文件 All checks passed

- [x] 0022 coverage baseline ratchet
  - id: task-0022
  - depends-on: task-0001
  - boundary: 新增 `scripts/check_coverage.py`、新增 `coverage-baseline.json`、`.github/workflows/ci.yml`、`pyproject.toml`（dev extras 增加 pytest-cov）。
  - 行为：脚本运行 `pytest --cov=nexuscli --cov-report=json`，读取 total percent；首次用 `--write-baseline` 写入 baseline，之后低于 baseline 则 exit 1；baseline 记录 Python 版本与生成命令。CI 只在 ubuntu 3.11 跑，避免三平台重复 7 分钟。不要把覆盖率写成 100% 目标；只锁“不低于 baseline”。
  - verify: `uv run python scripts/check_coverage.py`
  - evidence: uv run python scripts/check_coverage.py -> exit 0: 2011 passed, 12 skipped in 551.36s (Python 3.11.16), coverage total 88.70% (14381/16213) ≥ baseline 88.70% (+0.00%)；全量套件 0 failed；ruff format/check 对 3 个改动 .py 全部通过；ci.yml YAML safe_load 通过

- [x] 0023 mypy ratchet
  - id: task-0023
  - depends-on: task-0001
  - boundary: 新增 `scripts/check_mypy_ratchet.py`、新增 `mypy-baseline.json`、`.github/workflows/ci.yml`、`pyproject.toml`（固定 mypy 版本）。
  - 行为：脚本运行 `uv run --with mypy mypy src/nexuscli`，统计错误数、`ignore_errors` 模块数与被忽略行数；任一指标变差则 exit 1；baseline 记录 mypy/Python 版本。CI 用脚本替换裸 mypy 命令；仍保留 `mypy` 原始输出便于定位。
  - verify: `uv run python scripts/check_mypy_ratchet.py`
  - evidence: uv run python scripts/check_mypy_ratchet.py -> exit 0, verify exit 0: Found 7 errors in 4 files (checked 140 source files), OK errors 7 ≤ baseline 7(+0) / ignored_modules 30 ≤ 30(+0) / ignored_lines 6 ≤ 6(+0); 棘轮牙齿已验证——doctored baseline(errors=6) 与 mypy 版本不匹配两种情形均 exit 1；ruff format --check/check 对 scripts/check_mypy_ratchet.py 全部通过；ci.yml YAML safe_load 通过；uv lock --check 通过

- [x] 0024 pty SIGINT、并发恢复测试与 testing-matrix 更新
  - id: task-0024
  - depends-on: task-0015
  - boundary: 新增 `tests/test_pty_sigint.py`、新增 `tests/test_concurrent_recovery.py`、`docs/testing-matrix.md`。
  - 行为：pty 测试自建 pty 并应答 `ESC[6n` CPR，发送 SIGINT，断言进程退出且不产生 `SystemExit(130)` 误报；并发恢复测试覆盖 plan/team 并行 + journal 恢复，断言无重复副作用、无 history 串线。`docs/testing-matrix.md` 更新到当前 HEAD，列出 pty/并发/恢复/workbench 的新证据；Windows 上 skip 并注明原因。
  - verify: `uv run pytest -q tests/test_pty_sigint.py tests/test_concurrent_recovery.py`
  - evidence: uv run pytest -q tests/test_pty_sigint.py tests/test_concurrent_recovery.py -> exit 0, 5 passed (test_pty_sigint 2 + test_concurrent_recovery 3); ruff format 2 files reformatted→clean, ruff check All checks passed!

- [x] 0025 hermetic eval 进入 CI 门禁
  - id: task-0025
  - depends-on: task-0001
  - boundary: `.github/workflows/ci.yml`、`docs/eval.md`。
  - 行为：CI 增加 `uv run nexuscli eval run --all`（hermetic，离线，fake LLM）；任一场景失败即 job 失败；报告写到 `.nexuscli/evals` 并作为 artifact 上传。不要跑 `--real`（需要 API key 与预算）。文档说明本地如何复跑、如何看 regression。
  - verify: `uv run nexuscli eval run --all`
  - evidence: uv run nexuscli eval run --all -> exit 0, total=9 passed=9 failed=0 regressions=0, report written to .nexuscli/evals/last-report.json; ci.yml YAML parses (jobs: test/eval/frontend), eval.md fences balanced

## 阶段 C：平台能力与生态

- [x] 0026 OS 沙箱抽象 + macOS seatbelt
  - id: task-0026
  - depends-on: task-0001
  - boundary: 新增 `src/nexuscli/policy/os_sandbox.py`、`src/nexuscli/config.py`、`src/nexuscli/tools/commands.py`（或 `tools/executor.py` 的调用点）、新增 `tests/test_os_sandbox.py`、`docs/permissions.md`。
  - 行为：定义 `SandboxBackend` 协议（probe/wrap/available）；config 增加 `sandbox.backend = "none" | "seatbelt"`，默认 `none`；`seatbelt` 在 macOS 用 `sandbox-exec` 包装命令，限制写域与网络（按最小可用 profile），非 macOS 返回 typed `OperationUnsupported`；请求了不可用后端时 fail-closed，不静默退回 none。测试用 fake runner 断言 profile 生成、参数转义、不可用错误；真实 `sandbox-exec` 测试在 macOS 上 skip 条件化。
  - verify: `uv run pytest -q tests/test_os_sandbox.py`
  - evidence: uv run pytest -q tests/test_os_sandbox.py -> exit 0, 28 passed（含 4 个真实 sandbox-exec 用例在本机 macOS 实跑：probe 通过、cwd 内写入成功、cwd 外写入被拒、loopback 连接被拒+未沙箱对照成功）；ruff format --check 4 files already formatted、ruff check All checks passed!（4 个改动 .py）；附带回归：test_bash_timeout+test_sandbox_guard+test_config+test_process_group+test_slash_commands+test_slash_injection_security 共 52 passed、test_docs_integrity 5 passed、test_doctor_surface+test_runtime_static+test_permission_mode 9 passed

- [x] 0027 Linux bubblewrap/Landlock 后端
  - id: task-0027
  - depends-on: task-0026
  - boundary: 新增 `src/nexuscli/policy/linux_sandbox.py`、`src/nexuscli/policy/os_sandbox.py`、新增 `tests/test_linux_sandbox.py`、`docs/permissions.md`。
  - 行为：实现 `bubblewrap` 与 `landlock` 探测（`shutil.which` / 内核 ABI），优先 bubblewrap；缺依赖时返回 typed `OperationUnsupported` 并带安装提示；config 支持 `sandbox.backend = "bubblewrap" | "landlock"`；包装命令保留 cwd、只读根、可写 workspace、网络开关。测试用 fake binary/内核探测覆盖可用/不可用/权限拒绝；真实探测在 Linux 上条件化。
  - verify: `uv run pytest -q tests/test_linux_sandbox.py`
  - evidence: uv run pytest -q tests/test_linux_sandbox.py -> exit 0, 57 passed, 10 skipped（10 个为 Linux 条件化真实 bwrap/Landlock 冒烟，本机 macOS 按 skipif 跳过）；附带回归 tests/test_os_sandbox.py 28 passed、两文件合计 85 passed；ruff format 2 reformatted+1 unchanged、ruff check All checks passed!（3 个改动 .py）、mypy 两源文件 Success

- [x] 0028 会话树：parent/branch 元数据与 `/tree`
  - id: task-0028
  - depends-on: task-0001
  - boundary: `src/nexuscli/session/store.py`、`src/nexuscli/entrypoints/commands/session_cmds.py`、`docs/sessions.md`、新增 `tests/test_session_tree.py`。
  - 行为：会话 JSONL 增加可选 `parent_id`、`branch`、`created_at` 字段；`fork` 记录父会话与分支名；`/tree` 输出当前会话的父子/分支树（文本即可）；旧会话无字段时仍可读，默认视为根。测试覆盖 fork、嵌套 fork、旧格式读取、`/tree` 输出。
  - verify: `uv run pytest -q tests/test_session_tree.py`
  - evidence: uv run pytest -q tests/test_session_tree.py -> exit 0, 11 passed in 0.15s（uv run pytest -q tests/test_session_tree.py，退出码 0）

- [x] 0029 结构化压缩与文件台账
  - id: task-0029
  - depends-on: task-0001
  - boundary: `src/nexuscli/context/manager.py`、`src/nexuscli/agent/query.py`、`src/nexuscli/agent/agent.py`、`docs/memory-context.md`、新增 `tests/test_structured_compaction.py`。
  - 行为：压缩摘要使用固定小节（Goal / Constraints / Progress / Key Decisions / Next Steps / Critical Context），摘要角色必须是 user；维护“读写过的文件”台账（路径 + 最近操作 + 关键结论），跨多次压缩累计；旧摘要格式仍可解析。测试构造长会话，断言摘要含六节、文件台账保留、压缩后仍能回答关键约束。
  - verify: `uv run pytest -q tests/test_structured_compaction.py`
  - evidence: uv run pytest -q tests/test_structured_compaction.py -> exit 0, 10 passed（六节顺序/关键约束存活/台账 path+op+note/同路径最近操作覆盖/跨两次压缩+跨 manager 累计/parse 往返/prepare 自动路径/旧格式解析并并入 Critical Context/900 字符预算保闭合标签/错误结果入 Critical Context）；回归 test_context+test_overflow_retry+test_agent_overflow_parity+test_repl+test_agent_turns 共 62 passed（含 test_context.py:229-230 Focus 行位置断言不受影响）；ruff format 2 files reformatted 后复跑 verify 仍 10 passed、ruff check All checks passed!

- [x] 0030 插件包格式：用户级、命名空间、工具组件
  - id: task-0030
  - depends-on: task-0002
  - boundary: `src/nexuscli/plugins/loader.py`、`src/nexuscli/plugins/__init__.py`、`src/nexuscli/config.py`、`docs/plugins.md`、新增 `tests/test_plugin_packages.py`。
  - 行为：除 `<cwd>/.nexuscli/plugins/<name>` 外支持 `~/.nexuscli/plugins/<name>`；组件命名空间为 `<plugin>.<component>`；新增 tool 组件（声明 name/description/parameters/handler），注册后必须走同一 `ToolExecutor` 审批链；发现阶段只读 manifest，不执行代码；项目级插件默认需要审批。测试覆盖用户级加载、命名空间冲突、tool 组件审批、发现不执行。
  - verify: `uv run pytest -q tests/test_plugin_packages.py`
  - evidence: uv run pytest -q tests/test_plugin_packages.py -> exit 0, 13 passed（tests/test_plugin_packages.py 全绿；附带回归 tests/test_plugins.py 9 passed、消费方套件 81 passed；ruff format/check 对 3 个改动 .py 文件 All checks passed）

- [x] 0031 Google Gemini 原生 provider
  - id: task-0031
  - depends-on: task-0001
  - boundary: `src/nexuscli/llm/factory.py`、新增 `src/nexuscli/llm/google.py`、`src/nexuscli/llm/model_profiles.py`、新增 `tests/test_google_provider.py`、`docs/config.md`。
  - 行为：新增 `provider="google"` 分支，使用 Gemini 原生 HTTP（generateContent/streamGenerateContent），支持文本与图片输入、tool calls、usage；模型能力按 `ModelProfile` 字段判定，不按模型名硬编码；未知模型走保守默认。测试用 fake HTTP transport，断言请求形状、流式解析、tool call 映射、错误翻译；不访问真实网络。
  - verify: `uv run pytest -q tests/test_google_provider.py`
  - evidence: uv run pytest -q tests/test_google_provider.py -> exit 0, 16 passed in 0.21s（tests/test_google_provider.py 全绿；ruff format/ruff check 对 4 个改动 .py 文件 All checks passed）

- [x] 0032 Ollama/llama.cpp 本地 provider
  - id: task-0032
  - depends-on: task-0031
  - boundary: `src/nexuscli/llm/factory.py`、新增 `src/nexuscli/llm/local.py`、`src/nexuscli/config.py`、`docs/config.md`、新增 `tests/test_local_provider.py`。
  - 行为：新增 `provider="ollama"` 与 `provider="llamacpp"`（或统一 `local` + base_url），默认 `http://127.0.0.1:11434/v1` / `http://127.0.0.1:8080/v1`；无 API key 时不发送 Authorization；复用 OpenAI-compatible 流式与 tool call；模型列表/能力探测失败时给出可读提示。测试用 fake HTTP server，断言 base_url、无 key、流式解析、连接失败错误。
  - verify: `uv run pytest -q tests/test_local_provider.py`
  - evidence: uv run pytest -q tests/test_local_provider.py -> exit 0, 8 passed in 17.85s；附加核验：相关既有套件 uv run pytest tests/test_config.py tests/test_google_provider.py -> 19 passed（factory/config 共享路径无回归）；uv run ruff format 4 个改动 .py -> '4 files left unchanged'，uv run ruff check -> 'All checks passed!'

- [x] 0033 文档站搜索与英文入口
  - id: task-0033
  - depends-on: task-0007
  - boundary: `site/build.py`、`README.en.md`、`docs/en/quickstart.md`（可再加 1–2 篇）、`tests/test_docs_contract.py`。
  - 行为：`site/build.py` 生成 `search-index.json`（标题/路径/正文摘要），站点加搜索框与结果列表；英文入口 `README.en.md` 链接中文 README 与英文 quickstart，不要求全文翻译；docs contract 测试断言索引文件存在、搜索 UI 引用、英文入口链接可达。
  - verify: `uv run --with markdown,pygments python site/build.py && uv run pytest -q tests/test_docs_contract.py`
  - evidence: uv run --with markdown,pygments python site/build.py && uv run pytest -q tests/test_docs_contract.py -> exit 0, 22 pages + search-index.json 生成成功; pytest 13 passed (10 既有 + 新增 test_search_index_exists_with_title_path_summary / test_build_script_wires_search_ui / test_english_entry_links_resolve); 附加核验: node --check 两个生成页的搜索 JS 均通过（doc 页 rel="../"、tutorial rel=""）、对真实索引的过滤/排序冒烟（权限→permissions、MCP→mcp+tools、无命中→0）、uv run ruff format '2 files left unchanged' + uv run ruff check 'All checks passed!'（site/build.py 与 tests/test_docs_contract.py）


## 阶段 D：真实运行反馈与体验优化

- [x] 0034 修复 `--mode plan` 假完成
  - id: task-0034
  - depends-on: task-0012, task-0013
  - boundary: `src/nexuscli/agent/plan_execute.py`、`src/nexuscli/agent/query.py`、`src/nexuscli/entrypoints/cli.py`、新增 `tests/test_plan_readonly.py`。
  - 行为：当 `config.policy.plan_mode` 为真时，planner 不得把写文件/改文件/跑会写盘命令的任务排进可执行计划；若 worker 仍被 plan 只读闸拒绝，任务状态必须是 `blocked`（带拒绝原因），不得标 `COMPLETED`；最终摘要不得声称成功，进程退出码非 0。`--yes` 只影响审批，不绕过 plan 只读闸。测试用 fake query/worker 返回被拒结果，断言状态、摘要、退出码；再跑现有 plan 测试防回归。
  - verify: `uv run pytest -q tests/test_plan_readonly.py tests/test_plan.py`
  - evidence: uv run pytest -q tests/test_plan_readonly.py tests/test_plan.py -> exit 0, 19 passed in 0.30s（新增 test_plan_readonly.py 10 + test_plan.py 9）；相邻回归 15 个测试文件 130 passed（含 tests/test_lane_c_modes.py）；uv run ruff check All checks passed! / ruff format --check 3 files already formatted

- [x] 0035 workbench dist 从仓库/包根解析
  - id: task-0035
  - depends-on: task-0004
  - boundary: `src/nexuscli/runtime/api.py`、`src/nexuscli/entrypoints/cli.py`、新增 `tests/test_workbench_dist.py`、`tests/test_runtime_static.py`。
  - 行为：`_workbench_dist_root()` 按 env `NEXUSCLI_WORKBENCH_DIST` → 仓库根 `frontend/dist` → 包内 `nexuscli/frontend/dist` → 旧 `cwd/frontend/dist` 顺序解析；每步检查 `index.html` 是否存在。空态页列出已尝试路径与修复指引，不再只说“未构建”。`--cwd` 只决定任务目录，不决定 dist 根。测试用临时目录与 monkeypatch 覆盖四种顺序、缺失 index、空态文案。
  - verify: `uv run pytest -q tests/test_workbench_dist.py tests/test_runtime_static.py`
  - evidence: uv run pytest -q tests/test_workbench_dist.py tests/test_runtime_static.py -> exit 0, 19 passed (新增 test_workbench_dist.py 12 + test_runtime_static.py 7), exit 0; uv run ruff format 3 files left unchanged / ruff check All checks passed!（改动 .py 共 3 个）

- [x] 0036 headless 被拦截时可机读
  - id: task-0036
  - depends-on: task-0002
  - boundary: `src/nexuscli/entrypoints/cli.py`、`src/nexuscli/agent/agent.py`、`src/nexuscli/agent/results.py`、新增 `tests/test_headless_status.py`、`docs/permissions.md`。
  - 行为：`-p --json` 增加 `status: "ok" | "blocked" | "failed"` 与可选 `blocked_reason`；审批拒绝/plan 只读拦截记为 `blocked`，异常记为 `failed`。默认退出码保持 0 兼容；新增 `--fail-on-blocked`，在 blocked/failed 时 exit 2。非 JSON 模式在 stderr 打印一行状态。测试用 fake agent 覆盖 ok/blocked/failed、默认/开关退出码、JSON 字段。
  - verify: `uv run pytest -q tests/test_headless_status.py`
  - evidence: uv run pytest -q tests/test_headless_status.py -> exit 0, 22 passed: fake agent 覆盖 ok/blocked/failed、默认/开关退出码（blocked 默认 0、异常默认 1、0034 blocked plan 默认 1、--fail-on-blocked 一律 2）、JSON status/blocked_reason 字段、非 JSON stderr 一行 status；另回归 test_cli_single_prompt+test_plan_readonly+test_cost_unknown+test_cli 共 54 passed；ruff format --check 3 files already formatted / ruff check All checks passed!

- [x] 0037 未知模型价格显式 unknown
  - id: task-0037
  - depends-on: task-0001
  - boundary: `src/nexuscli/llm/pricing.py`、`src/nexuscli/agent/agent.py`、`src/nexuscli/entrypoints/cli.py`、`docs/config.md`、新增 `tests/test_cost_unknown.py`。
  - 行为：模型不在内置价格表且用户未配置 `llm.prices` 时，`--json` 的 `cost` 为 `null`，并给 `cost_note: "unknown model price; configure llm.prices"`；`session_usage.cost_usd`/`cost_cny` 为 `null`，不再伪造 0.0；已知模型行为不变。文档给出 `llm.prices` 配置示例。测试覆盖未知/已知/用户配置三种情况。
  - verify: `uv run pytest -q tests/test_cost_unknown.py`
  - evidence: uv run pytest -q tests/test_cost_unknown.py -> exit 0, 5 passed：未知模型 → cost null + cost_note + session_usage 双口径 cost null；已知/用户配置/已知无cost/离线demo(有cost无profile) 均保持原契约；另回归 test_cli_single_prompt+test_session_usage+test_eval_cost_p1+test_cli+test_round2_closeout_fixes 共 54 passed，ruff format/check 全过

- [x] 0038 固定 prompt/tool 开销可诊断
  - id: task-0038
  - depends-on: task-0001
  - boundary: `src/nexuscli/agent/agent.py`、`src/nexuscli/tools/registry.py`、`src/nexuscli/config.py`、`src/nexuscli/entrypoints/cli.py`、`docs/config.md`、新增 `tests/test_prompt_budget.py`。
  - 行为：Agent 在每次请求前记录 `system_chars`、`tools_chars`、`history_chars`、`tool_count`，`--json` 的 `usage.prompt_breakdown` 输出这些字段与 `estimated_tokens`（ceil(chars/4)）；`doctor --token-budget` 打印当前配置的基线。新增 `agent.prompt_profile: "full" | "lean"`（默认 full），lean 只保留核心工具 schema（read_file/write_file/edit_file/bash/list_dir/grep/glob/update_plan），并在 breakdown 中反映减少量；不改变默认行为。测试用 fake LLM 断言字段、总数、lean 模式 `tool_count` 与 `tools_chars` 下降。
  - verify: `uv run pytest -q tests/test_prompt_budget.py`
  - evidence: uv run pytest -q tests/test_prompt_budget.py -> exit 0, 13 passed: prompt_breakdown 字段/总数/ceil(chars/4)、Agent 每请求前记录（last_prompt_breakdown 与 wire 交叉核对、不进事件流保 react≡query parity）、lean 模式 tool_count/tools_chars 下降（25→7）、prompt_profile 解析（默认 full/lean/非法值回落）、-p --json usage.prompt_breakdown（react 输出、未测到请求保持旧契约）、_MeasuredLlmClient 委托与测量、doctor --token-budget 基线（full/lean）；另回归 13 个相邻测试文件 113 passed（含 test_agent_overflow_parity），ruff format --check 5 files already formatted / ruff check All checks passed!

- [x] 0039 明确 `--mode plan` 的只读语义
  - id: task-0039
  - depends-on: task-0034, task-0007
  - boundary: `src/nexuscli/entrypoints/cli.py`、`README.md`、`docs/plan-execute.md`、`docs/permissions.md`、`tests/test_docs_contract.py`。
  - 行为：CLI help 写明 `--mode plan` 是只读规划、不会执行写入、`--yes` 在该模式无效；检测到 `--mode plan --yes` 时打印一条 warning。README 与两份 docs 区分三种机制：`--mode plan`（只读）、`/plan`（交互式 plan-execute）、Shift+Tab（REPL 只读态）。docs contract 测试断言关键词与链接存在。
  - verify: `uv run pytest -q tests/test_docs_contract.py`
  - evidence: uv run pytest -q tests/test_docs_contract.py -> exit 0, 17 passed in 0.29s（含新增 4 个 task_0039 断言）；ruff format --check 与 ruff check 对两个改动 .py 文件均通过


---

**当前进度**：由脚本计算，无需手动维护
