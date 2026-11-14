# 2026-11-13_fix-project-trust-boundary - Completion Summary

## Delivery
- task-0001 项目 config 安全键信任门
- task-0002 项目 MCP server 信任门与环境清洗
- task-0003 headless hooks 接入信任门
- task-0004 slash 注入权限主体映射
- task-0005 路径权限规则归一化
- task-0006 写域长选项 `--flag=value`
- task-0007 审计归因 never/auto
- task-0008 只读分类 secret 路径与 git rev:path
- task-0009 MCP resource/prompt 审批
- task-0010 文档同步信任边界
- task-0011 端到端攻击 fixture
- task-0012 项目层 sandbox/telemetry 信任门
- task-0013 既有测试对齐新信任模型
- task-0014 静态门收口（ruff / format / mypy）
- task-0015 SSE 顺序依赖与全量回归

## Evidence
- Final-tree verifies passed; see runs.jsonl.

## Decisions
- D1 |  项目层安全键采用指纹信任门，未信任时忽略并告警，不静默合并 | by: F1 | reversal: revert commit
- D2 |  项目 MCP 与用户 MCP 分层：用户层始终生效，项目层需信任 | by: F2 | reversal: revert commit
- D3 |  slash 注入保留内部工具名，权限/审计主体映射到 bash/execute_command | by: F3 | reversal: revert commit
- D4 |  信任入口只做交互式确认，headless 一律 fail-closed，不新增 CLI 子命令 | by: F4 | reversal: revert commit
- D5 |  用户 2026-11-14 验收要求「除 GitHub billing 外把其余问题都解决并合并」，据此把整改范围扩到本包直接相关的收尾面（sandbox/telemetry 信任键、既有测试对齐、静态门、SSE 顺序依赖、全量回归），V2 由 strict 改为 adjacent-fixes，新增 4 个 emergent 任务 | by: user 2026-11-14 验收修复授权 | reversal: revert commit
- D6 |  P4 处置：29 个旧测试编码旧信任模型，改为用户层配置或显式写入 ProjectTrustStore/MCP trust 记录，不放宽信任门；P4 记录里的「28」按实测 29 修正 | by: user 2026-11-14 验收修复授权 | reversal: revert commit
- D7 |  P1–P3（容器/Windows 沙箱与远程 RBAC、PyPI/demo/branch protection/CI billing、非 Python LSP 与 git undo）经用户授权按范围外接受，不阻塞本包交付；其中 GitHub Actions billing 只影响托管 CI 配额，不影响本地 pytest/ruff/mypy | by: user 2026-11-14 验收修复授权 | reversal: 后续独立包

## Parked

## Emergent tasks
- task-0012 项目层 sandbox/telemetry 信任门
- task-0013 既有测试对齐新信任模型
- task-0014 静态门收口（ruff / format / mypy）
- task-0015 SSE 顺序依赖与全量回归

## Lessons
- No retained lessons.
- Archived: 2026-11-14T10:11:34+00:00

## Git
- Commit interval: 2edc69a1fc5c852ce5d1c19d8ef698c9e1d456dd
