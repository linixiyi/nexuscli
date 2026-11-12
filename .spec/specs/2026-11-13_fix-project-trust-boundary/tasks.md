# 项目信任边界与执行安全 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 每个任务先写会失败的测试，再修到绿；不允许 skip/xfail，不允许改断言迁就实现。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在仓库根目录可执行。
- 0004 → 0005 与 0002 → 0009 串行，避免同一文件并发改；0010/0011 在 0001–0009 全部完成后才能开始。
- 复用现有 `hooks/trust.py` 的指纹模式；不引入新依赖；不改变已通过的 plan worker 只读语义。

## 阶段 A：项目信任边界

- [x] task-0001 项目 config 安全键信任门
  - id: task-0001
  - boundary: src/nexuscli/config.py, new:src/nexuscli/policy/project_trust.py, new:tests/test_project_trust.py
  - origin: planned
  - expect: red
  - behavior: 项目层 `.nexuscli/config.json` 与项目 `.env` 中，`policy.*`（至少 hitl_mode、path_guard_enabled、command_guard_enabled）、`permissions.allow`、`features.audit_log`、`llm.base_url/api_key/provider` 属于安全敏感键；无项目信任指纹时这些键不得合并，保留用户层/默认值，并给一次可见 warning。信任指纹用 canonical JSON + sha256（可复用 hooks/trust.py 的模式或抽到新 `policy/project_trust.py`），交互式确认后可记忆；无 TTY 的 `-p`/serve 一律 fail-closed。不得改动未列入安全键的项目配置（如渲染/提示词）。
  - tests: 新增 `tests/test_project_trust.py`：① 恶意项目 config 不能改 hitl/guards/base_url；② 项目 `.env` 的 `NEXUSCLI_HITL`/`NEXUSCLI_BASE_URL` 不能生效；③ 无 TTY 时无信任一律忽略；④ 信任后同一键可生效；⑤ 非安全键不受影响。
  - verify: `uv run pytest -q tests/test_project_trust.py`

- [x] task-0002 项目 MCP server 信任门与环境清洗
  - id: task-0002
  - boundary: src/nexuscli/mcp/config.py, src/nexuscli/mcp/client.py, new:tests/test_mcp_project_trust.py
  - origin: planned
  - expect: red
  - behavior: `load_mcp_server_specs`/`read_mcp_config_entries` 区分用户层与项目层；项目层 enabled server 必须通过项目信任指纹才进入有效列表，未信任时只保留用户层 server。stdio `StdioServerParameters.env` 改为 `sanitize_environment(overrides=spec.env)`，禁止 `{**os.environ, **spec.env}` 把密钥形态变量全量传给子进程；`spec.env` 的显式值仍然保留。
  - tests: 新增 `tests/test_mcp_project_trust.py`：① 无信任的项目 mcp.json 不 spawn；② 信任后可加载；③ 用户层 server 不受影响；④ 子进程 env 不含 `*_API_KEY`/token 形态；⑤ 非 dict/坏配置行为不回归。
  - verify: `uv run pytest -q tests/test_mcp_project_trust.py`

- [x] task-0003 headless hooks 接入信任门
  - id: task-0003
  - boundary: src/nexuscli/entrypoints/cli.py, src/nexuscli/runtime/api.py, src/nexuscli/hooks/trust.py, new:tests/test_hooks_headless_trust.py
  - origin: planned
  - expect: red
  - behavior: 把 REPL 已有的项目 hooks 信任门抽成可复用函数，并接入 `-p`（cli.py）与 serve（runtime/api.py）启动路径；无 TTY 且无已信任指纹时，项目层 hooks 一律不执行并 warning，用户层 hooks 不受影响。`hooks_cmds.py` 的 REPL 行为不得回归。
  - tests: 新增 `tests/test_hooks_headless_trust.py`：① `-p` 路径无信任不执行项目 hook；② serve 路径同样；③ 已信任指纹可执行；④ 用户 hooks 始终执行；⑤ 无 TTY 确认函数默认拒绝。
  - verify: `uv run pytest -q tests/test_hooks_headless_trust.py`

## 阶段 B：权限/路径/执行链

- [x] task-0004 slash 注入权限主体映射
  - id: task-0004
  - boundary: src/nexuscli/entrypoints/slash_commands.py, src/nexuscli/policy/permission_rules.py, new:tests/test_slash_injection_policy.py
  - origin: planned
  - expect: red
  - behavior: `!`cmd`` 注入仍然以内部工具名 `slash_injection` 进 registry，但权限规则与 PreToolUse hook 评估时使用命令主体（`bash`，或 `execute_command` 的等价主体）；审计同时记录内部名与真实主体，deny/ask/allow 优先级不变。CommandGuard 仍先于执行器。
  - tests: 新增 `tests/test_slash_injection_policy.py`：① `deny:["bash"]` 时片段返回 refused 且不执行；② `deny:["execute_command"]` 同样；③ 匹配 `bash` 的 PreToolUse hook 能阻断；④ allow 规则仍能放行；⑤ 审计含真实主体。
  - verify: `uv run pytest -q tests/test_slash_injection_policy.py`

- [ ] task-0005 路径权限规则归一化
  - id: task-0005
  - boundary: src/nexuscli/policy/permission_rules.py, new:tests/test_permission_path_normalization.py
  - depends-on: task-0004
  - origin: planned
  - expect: red
  - behavior: path 规则匹配同时考虑 raw、POSIX 归一化（折叠 `./`、`a/../`、重复 `/`）与解析后的相对路径；任一等价形态命中即视为命中，保留 raw 形态兼容既有规则。不得把绝对路径误当成相对路径或反向放宽。
  - tests: 新增 `tests/test_permission_path_normalization.py`：`./.env`、`a/../.env`、`.//AGENTS.md`、`src/./x.py` 均被同一条 deny 命中；合法 `src/x.py` 不被误伤；绝对路径与 symlink 行为不回退。
  - verify: `uv run pytest -q tests/test_permission_path_normalization.py`

- [ ] task-0006 写域长选项 `--flag=value`
  - id: task-0006
  - boundary: src/nexuscli/policy/fs_scope.py, src/nexuscli/tools/builtins.py, new:tests/test_fs_scope_long_flags.py
  - origin: planned
  - expect: red
  - behavior: `_scan_paths` 对 `--name=value` 拆分，仅当 `name` 在 value_flags 中才把 value 加入 targets；补 `mv/cp/copy/move` 的 `--target-directory`。`builtins.py` 内层写域提示使用调用方真实工具名，不再硬编码 `bash`，使 `allow:["execute_command"]` 不再二次弹窗。`-t /etc` 等既有形式不回归。
  - tests: 新增 `tests/test_fs_scope_long_flags.py`：① `mv --target-directory=/etc x` → outside；② `cp --target-directory=/etc/hosts x` → outside；③ `mv -t /etc x` 仍 outside；④ 普通 `--verbose` 不误判；⑤ execute_command + allow 不二次弹写域。
  - verify: `uv run pytest -q tests/test_fs_scope_long_flags.py`

## 阶段 C：审计/只读/MCP 审批

- [ ] task-0007 审计归因 never/auto
  - id: task-0007
  - boundary: src/nexuscli/tools/executor.py, new:tests/test_audit_attribution.py
  - origin: planned
  - expect: red
  - behavior: `hitl_mode=never` 下无需审批的自动放行记为 `policy-never`，无审批回调的 auto 直通记为 `auto`（或既有明确值），不得记成 `hitl`；`--yes`/run-scope/readonly-rule/permission-rule 的既有归因顺序保持不变。
  - tests: 新增 `tests/test_audit_attribution.py`：① never + write_file → policy-never；② auto 无回调直通 → 非 hitl；③ `--yes` 仍 hitl；④ run-scope 仍 run-scope；⑤ readonly 仍 readonly-rule。
  - verify: `uv run pytest -q tests/test_audit_attribution.py`

- [ ] task-0008 只读分类 secret 路径与 git rev:path
  - id: task-0008
  - boundary: src/nexuscli/policy/bash_readonly.py, new:tests/test_bash_readonly_exfil.py
  - origin: planned
  - expect: red
  - behavior: 扩充 `SECRET_PATH_SEGMENTS`（至少 `.config/gh`、`.git-credentials`、`.config/gcloud`、`.azure`）；`git show/ diff/ log` 等只读子命令的 `rest` 中出现 `<rev>:<path>` 时复用 `_looks_secret`，命中即不判只读；`date` 的纯数字/`MMDDhhmm` 设时钟形态、`file -C` 也拒绝。已有 `.ssh`/`.env`/`--output` 行为不回归。
  - tests: 新增 `tests/test_bash_readonly_exfil.py`：`cat ~/.git-credentials`、`cat ~/.config/gh/hosts.yml`、`git show HEAD:.env`、`date 010100002026`、`file -C -m /tmp/m` 均 False；正常 `git show HEAD:README.md`、`date`、`file x` 仍 True。
  - verify: `uv run pytest -q tests/test_bash_readonly_exfil.py`

- [ ] task-0009 MCP resource/prompt 审批
  - id: task-0009
  - boundary: src/nexuscli/mcp/client.py, new:tests/test_mcp_resource_approval.py
  - depends-on: task-0002
  - origin: planned
  - expect: red
  - behavior: `read_resource`/`get_prompt` 不再本地声明只读免审批；改为 `requires_approval=True`（或至少进入审批链与审计）。`list_resources`/`list_prompts` 等纯列目录工具可保持只读。plan 模式下这些调用仍被只读闸拒绝。
  - tests: 新增 `tests/test_mcp_resource_approval.py`：① read_resource 在 auto 模式触发一次审批；② 审计出现该调用；③ plan 模式被拒；④ list_resources 保持只读不弹审批；⑤ 未知 URI/失败不改变拒绝语义。
  - verify: `uv run pytest -q tests/test_mcp_resource_approval.py`

## 阶段 D：文档与端到端

- [ ] task-0010 文档同步信任边界
  - id: task-0010
  - boundary: README.md, docs/permissions.md, docs/mcp.md, docs/tools.md, new:tests/test_security_docs.py
  - depends-on: task-0001, task-0002, task-0003, task-0004, task-0005, task-0006, task-0007, task-0008, task-0009
  - origin: planned
  - expect: red
  - behavior: 更新 README 安全段与 docs/permissions.md、docs/mcp.md、docs/tools.md：项目层安全键需信任、项目 MCP 需信任且 env 清洗、headless hooks 无信任不执行、slash 注入受 bash deny/hook 约束、路径归一化与 `--flag=value`、MCP resource/prompt 审批、审计归因。不得把未实现的远程/RBAC 说成已支持。
  - tests: 新增 `tests/test_security_docs.py`，断言上述关键词与链接存在；不依赖网络。
  - verify: `uv run pytest -q tests/test_security_docs.py`

- [ ] task-0011 端到端攻击 fixture
  - id: task-0011
  - boundary: new:tests/test_project_trust_e2e.py
  - depends-on: task-0001, task-0002, task-0003, task-0004, task-0005, task-0006, task-0007, task-0008, task-0009
  - origin: planned
  - expect: red
  - behavior: 在一个临时 HOME + 临时仓库里同时放置恶意项目 config（hitl never/guards off/base_url evil）、恶意项目 mcp.json（enabled /bin/sh）、项目 hooks、用户级 slash `!`cmd``；用 fake LLM/无网络路径启动 `-p` 与 serve，断言：权限不降级、MCP 不 spawn、hooks 不执行、slash deny 生效、无副作用、无敏感 env 进入子进程。测试必须可离线重复运行。
  - verify: `uv run pytest -q tests/test_project_trust_e2e.py`

---

**当前进度**：由脚本计算，无需手动维护
