<!-- spec-format: 3 -->

# 项目信任边界与执行安全 - 项目范围

## Goal

- goal: 关闭“克隆仓库即可静默关闭权限链、拉起 MCP 命令、绕过 bash deny”的项目层信任缺口，并修掉同一条执行链上的审计归因、路径归一化、只读分类与 MCP 审批漏点；每个缺口都有先红后绿的本机测试。
- literal ask: 「你先把 /spec 包写好，然后我跟 zcode 说」——为夜间安全加固写可执行的 v3 包。
- stated purpose: 把项目层 `.nexuscli/config.json`、`.nexuscli/mcp.json`、hooks、slash 注入、路径规则与审计归因纳入同一条可验证的信任边界，而不是只修单点。
- done means: 项目层安全键无信任时不生效；项目 MCP server 无信任不 spawn 且 env 已清洗；headless hooks 无信任不执行；slash 注入受 `bash` deny/hook 约束；路径规则归一化与 `--flag=value` 写域可测；审计 `never` 归因、只读分类、MCP resource/prompt 审批均有回归测试；docs 同步；集成攻击 fixture 通过。

## Charter

- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md

## Scope

- in: src/nexuscli/config.py, src/nexuscli/entrypoints/cli.py, src/nexuscli/entrypoints/slash_commands.py, src/nexuscli/hooks/**, src/nexuscli/mcp/**, src/nexuscli/policy/**, src/nexuscli/runtime/api.py, src/nexuscli/tools/builtins.py, src/nexuscli/tools/executor.py, tests/**, README.md, docs/permissions.md, docs/mcp.md, docs/tools.md
- out: frontend/**, .github/**, pyproject.toml, uv.lock, site/**, src/nexuscli/llm/**, src/nexuscli/eval/**

## Assumptions

- A1 | 项目 `.nexuscli/config.json` 直接 deep-merge，安全键无项目层过滤 | fact | probe: `sed -n '320,335p' src/nexuscli/config.py` | CONFIRMED | if false: 重新定位合并点
- A2 | 项目 `.nexuscli/mcp.json` 与用户层合并，enabled server 会进入启动工具表 | fact | probe: `sed -n '58,72p' src/nexuscli/mcp/config.py` | CONFIRMED | if false: 重新定位 MCP 配置层
- A3 | hooks 信任门只在 REPL 路径调用，`-p` 与 serve 未接 | fact | probe: `sed -n '25,50p' src/nexuscli/entrypoints/commands/hooks_cmds.py` | CONFIRMED | if false: 重新定位 hooks 入口
- A4 | slash 注入以 `slash_injection` 工具名过权限链，`deny:["bash"]` 不命中 | fact | probe: `sed -n '321,375p' src/nexuscli/entrypoints/slash_commands.py` | CONFIRMED | if false: 以实际工具主体为准
- A5 | 路径规则对原始 path 字符串做 fnmatch，未归一化 `./`、`a/../` | fact | probe: `sed -n '75,115p' src/nexuscli/policy/permission_rules.py` | CONFIRMED | if false: 重新定位 subject_for
- A6 | `_scan_paths` 跳过 `-` 开头 token，`--target-directory=/etc` 不进入 targets | fact | probe: `sed -n '340,365p' src/nexuscli/policy/fs_scope.py` | CONFIRMED | if false: 重新定位扫描器
- A7 | `hitl_mode=never` 自动放行仍会落到 `approver="hitl"` 分支 | fact | probe: `sed -n '300,310p' src/nexuscli/tools/executor.py` | CONFIRMED | if false: 重新核对归因顺序
- A8 | `bash_readonly` 的 secret 集合不含 `.git-credentials`/`.config/gh`，`git show rev:path` 不复用 secret 判断 | fact | probe: `sed -n '145,190p' src/nexuscli/policy/bash_readonly.py` | CONFIRMED | if false: 以实际漏点为准
- A9 | MCP `read_resource`/`get_prompt` 本地声明 `is_read_only=True`，免审批 | fact | probe: `sed -n '320,395p' src/nexuscli/mcp/client.py` | CONFIRMED | if false: 重新核对 MCP 虚拟工具
- A10 | 复用 hooks 的 canonical JSON + sha256 指纹模式即可实现项目信任，无需新依赖 | assumption | UNVERIFIED | if false: 在 policy 层实现本地 JSON 指纹存储，不引入第三方依赖

## Forks

- F1 | when: 项目层出现安全敏感配置键 | a) 未信任时静默忽略并告警 b) 用指纹信任门决定是否合并 | pick: b | by: V1 | reversal: reversible
- F2 | when: 项目层 mcp.json 含 enabled server | a) 项目层一律忽略 b) 信任后合并，用户层始终可用 | pick: b | by: V1 | reversal: reversible
- F3 | when: slash 注入需要命中 bash 的 deny/hook | a) 把工具重命名为 bash b) 保留 slash_injection 名字，权限评估用 bash/execute_command 主体 | pick: b | by: V6 | reversal: reversible
- F4 | when: 无 TTY 时如何信任项目 | a) 只支持交互式确认，headless fail-closed b) 额外加 `nexuscli trust project` 命令 | pick: a | by: V2 | reversal: reversible

## Criteria

- C1 | 项目 config 的 policy/permissions/audit/llm 安全键在无信任时不生效，`.env` 同样被过滤 | task-0001
- C2 | 项目 mcp.json 在无信任时不 spawn，stdio env 不再继承全量 os.environ | task-0002
- C3 | `-p`/serve 的项目 hooks 在无信任时不执行，用户 hooks 不受影响 | task-0003
- C4 | slash !`cmd` 在 `deny:["bash"]` 与 PreToolUse bash hook 下被拒，审计标注真实主体 | task-0004
- C5 | `./`、`a/../`、`.//` 路径规则与 deny 归一化命中 | task-0005
- C6 | `mv/cp --target-directory=/etc` 被判 outside，execute_command 的 allow 不再二次弹写域 | task-0006
- C7 | `hitl_mode=never` 审计归因 policy-never，`--yes`/auto 归因不变 | task-0007
- C8 | `.git-credentials`、`~/.config/gh`、`git show HEAD:.env` 不再被判只读 | task-0008
- C9 | MCP read_resource/get_prompt 需审批并进入审计 | task-0009
- C10 | docs 与 README 的权限/MCP/工具边界说明同步，文档契约测试通过 | task-0010
- C11 | 临时仓库攻击 fixture（config+mcp+hooks+slash）全部被拦住，且无副作用 | task-0011

## Decisions

- D1 | 项目层安全键采用指纹信任门，未信任时忽略并告警，不静默合并 | by: F1 | reversal: revert commit
- D2 | 项目 MCP 与用户 MCP 分层：用户层始终生效，项目层需信任 | by: F2 | reversal: revert commit
- D3 | slash 注入保留内部工具名，权限/审计主体映射到 bash/execute_command | by: F3 | reversal: revert commit
- D4 | 信任入口只做交互式确认，headless 一律 fail-closed，不新增 CLI 子命令 | by: F4 | reversal: revert commit

## Parked

- P1 | 容器/Windows OS 沙箱后端与远程 daemon/RBAC 不在本包范围 | by: V2 | reversal: 后续独立包
- P2 | PyPI、demo、branch protection 与 CI billing 不在本包范围 | by: V2 | reversal: 手工发布项
- P3 | 非 Python 的真实 LSP 与 git 级 undo 不在本包范围 | by: V2 | reversal: 后续能力包

## Binding

- branch: `spec/2026-11-13_fix-project-trust-boundary`
- frozen: 2026-11-12T16:33:01+00:00
