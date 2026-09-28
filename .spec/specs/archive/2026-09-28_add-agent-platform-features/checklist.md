# Agent 平台能力移植（hooks/子代理/权限规则/命令增强/compact/plan 模式） - 验收清单

## 假设与范围对齐
- [x] 无阻塞性待确认项；如有待确认项，已明确标记无需阻塞/适用外并给出理由
- [x] 范围内/范围外与实现一致
- [x] 本轮发现的可执行问题已回写任务包并完成，未甩给用户（6 项安全发现均已在流水线内修复并复验门禁）

## 简洁性
- [x] 没有未请求的扩展、抽象或配置化（权限单一 tool(pattern) 语法；hooks 仅 command 型；无新增运行时依赖——requirements/uv.lock 未动）
- [x] 当前方案保持最小可行

## 变更边界
- [x] 每项改动都能追溯到明确任务（git status 全部文件可映射到 F1-F6/集成/文档任务）
- [x] 没有无关重构或顺手清理（唯一计划外文件 `nul` 为子进程误产物，验收时已删除）

## 功能完整性
- [x] MVP 功能全部实现（F1-F6 六项，spec.md §3.1 已勾）
- [x] 边界条件已处理（空历史压缩、hook timeout null 兜底 60s、agent_type 不存在纠错、危险注入拒绝、win32 USERPROFILE 兼容）
- [x] 错误处理符合预期（fail-closed：never 态 ask 兜底 deny；plan 态非只读硬拒绝）

## 测试与验证
- [x] 核心逻辑有测试或等价证据（六功能测试文件 90 用例；全量 226 passed）
- [x] 关键路径已验证（executor 审批链×权限×hook×plan 叠加有交叉用例 tests/test_integration_platform.py）

## 文档同步
- [x] 受影响文档已更新，或记录适用外（README 六功能章节、TUTORIAL 5.4 平台能力速览）
- [x] `spec.md` / `tasks.md` / `checklist.md` 已同步

## 部署验证（如适用）
- [x] 本地运行正常，或已记录适用外理由（`uv run nexuscli --help` exit 0；交互式 REPL smoke 见手工验证栏的跟进项）
- [x] 构建成功，或已记录适用外理由（CLI 入口可加载全部新模块，无 import 错误）

## 边界回归
- [x] 越界负样本：boundary 外改动会被识别或回滚，未混入交付（`N/A：本轮 git status 逐文件核对全部落在 spec §5 允许清单内；llm/mcp/runtime/session/snapshot/web/image/plan/rag/sdk.py 零改动` 后勾选）
- [x] 顺序交换负样本：交换无依赖任务或调换检查节顺序，验收结论不变（`N/A：F4/F5 与 F1 并行无依赖已实证；F2/F3 依赖波次串行由门禁保证` 后勾选）
- [x] 旁路负样本：不存在绕过 `verify` 的完成入口，未运行的验证一律视为未通过（`N/A：全部 11 任务勾选前均已实跑 verify 命令；门禁由脚本 world.run 确定性执行` 后勾选）

## 验收证据
- 外部对标：以 task package 状态机为真，fresh package 不得直接进入实现态
- 脚本验证：`python check_spec_package.py --slug 2026-09-28_add-agent-platform-features --compact` → 收敛（11/11 任务完成，回填后复跑）
- 旧新对比：基线 415cb24（~141 tests）→ 当前 `uv run python -m pytest` → **226 passed in 17.40s**（净增 85；六功能测试文件 90 passed）
- 差异边界：git status 逐文件核对，改动全部在 spec §5 允许清单（config/policy/tools/hooks 新包/agent/entrypoints/context/tests/README/TUTORIAL/.spec）；llm/mcp/runtime/session/snapshot/web/image/plan/rag/sdk.py 未动
- 行为成效：F1 deny>ask>allow + 审计命中规则；F2 五事件 hooks（29 用例，exit 2 阻断/hook allow 不绕审批/timeout 兜底）；F3 子代理（深度 1 防递归、explore 只读白名单、审批透传）；F4 frontmatter/$1..$9/!`cmd`（CommandGuard 危险拒绝）；F5 compact_now 前后 token 统计 + focus 注入；F6 三态循环 + executor 层硬拒绝；安全评审 6 项发现（4 medium 2 low）经独立复核确认并全部修复、复跑门禁通过
- 构建：`uv run nexuscli --help` → exit 0
- 测试：`uv run python -m pytest` → 226 passed in 17.40s；`uv run python -m ruff check .` → All checks passed!；`uv run python -m ruff format --check .` → 104 files already formatted
- 手工验证：交互式 REPL smoke（真人 TTY：Shift+Tab 三态切换、/help 渲染、/compact 实机输出）未执行——记录为后续人工跟进项，不阻塞本轮验收（对应逻辑均有自动化测试覆盖）

---

**验收结果**：通过

遗留跟进（不阻塞验收）：交互式 REPL smoke（真人 TTY：Shift+Tab 三态切换、/help 渲染、/compact 实机输出）待人工执行，对应逻辑均已有自动化测试覆盖。
