# nexusCLI ZCode 差距存量台账任务包 - 验收清单

## 假设与范围对齐

- [x] 无阻塞性待确认项；spec.md 2.3 的两项待确认（P2 裁剪、mailbox 开放范围）均已标注不阻塞并给出当前假设（P2 全量交付；mailbox 仅子代理注册——均已按此实现并有测试钉住）
- [x] 范围内/范围外与实现一致：A 类 4 项、B 类 2 项、C 类 8 项、P2 14 项全部映射为任务或五枚举处置，与 tasks.md / spec.md 3.3 一致（对账见 tasks.md 各 evidence 行；/expert 并入 C8）
- [x] 本轮发现的可执行问题已回写任务包并完成，未甩给用户（八处出入①-⑧全部回写 tasks.md evidence：bootstrap 扩展、_task_output 读接线、audit_log.py trace_id 缝隙、悬空 grep 用例引用、factory 签名冲突方案 B、otel 框漏翻框等）

## 简洁性

- [x] 没有未请求的扩展、抽象或配置化（未做可插拔接口、常驻调度器、并行执行器；telemetry/debug_dump 均为单布尔开关默认关）
- [x] 当前方案保持最小可行（C8 仅串行拓扑序最小切片；插件仅本地目录形态）

## 变更边界

- [x] 每项改动都能追溯到明确任务（boundary 具体到路径；boundary 外改动均为已裁决并回写的出入，见 tasks.md evidence）
- [x] 没有无关重构或顺手清理（各波实现代理零无关改动自检 + 主会话验收抽查）
- [x] README/docs 更新全部落在 task-final-docs-sync，实现任务未各自触碰共享文档（W1 文档收口任务为编排方指令的波内唯一文档入口，证据在 tasks.md）

## 功能完整性

- [x] MVP 功能全部实现（P1 八项 + A1/A3 + B1 + 文档同步）
- [x] 边界条件已处理（未知 task_id、坏 manifest、缺 pypdf、SDK 缺失 no-op、rg 缺失回退、空 usage 库等各任务测试点——各 verify 命令全绿）
- [x] 错误处理符合预期（deny/ask/hook/hitl=always 三条拦截线语义不回退——tests/test_permissions.py、test_hooks.py、test_plan_mode.py 全程绿色）

## 测试与验证

- [x] 核心逻辑有测试或等价证据（每任务 verify 均为真实可运行命令；凭据类任务以 mock 验证协议层）
- [x] 关键路径已验证（六项平台能力测试文件全程绿色）

## 文档同步

- [x] README.md / TUTORIAL.md / docs/ 与实现一致（命令表、工具表、配置项、事件表逐项核对，无虚构；独立核查员抽查 12+ 条声明对照实现，3 处问题已修复；81+168 锚点核验 0 断链）
- [x] `spec.md` / `tasks.md` / `checklist.md` 已同步（功能框全勾、任务 26/26 勾选带 evidence、本清单为最终状态）

## 部署验证（如适用）

- [x] `uv sync --extra dev --frozen` 退出码 0（依赖变更后 lock 一致；2026-09-29 收尾实测：Checked 51 packages）
- [x] `uv run nexuscli --help` 或等价入口冒烟可用（2026-09-29 主会话实测：exit 0，命令面板正常渲染；`--version` → nexuscli 0.1.0）

## 边界回归

- [x] 越界负样本：N/A：全部实现经波次简报边界约束 + 主会话验收抽查；boundary 外改动共八处出入均已由编排方逐项裁决并回写 tasks.md evidence，无未声明的混入改动
- [x] 顺序交换负样本：N/A：波次间为真依赖（depends-on 链与文件冲突分析），交换不可行；验收口径（各任务 verify 命令）与顺序无关
- [x] 旁路负样本：每个任务勾选均附 verify 命令实跑尾行证据，无凭证据勾选；主会话两次漏翻复选框均被评审/规划代理抓出并当场修正，最终以 check_spec_package.py 脚本输出为准

## 验收证据

- 外部对标：以 task package 状态机为真，fresh package 不得直接进入实现态
- 脚本验证：收尾重跑 `python E:/Code/.refs/spec-skill/scripts/check_spec_package.py --slug 2026-09-29_add-zcode-backlog --root E:/Code/nexusCLI`（2026-09-29，输出见 tasks.md task-checklist-evidence 行：全部任务勾选、清单勾选、功能框勾选，无未通过门禁）
- 旧新对比：基线（2026-09-29 实测）`uv run python -m pytest` → 338 passed in 25.96s；`uv run ruff check .` → All checks passed!；`uv run ruff format --check .` → 110 files already formatted
- 差异边界：新增模块 telemetry/、plugins/、workflow/、agent/mailbox.py、agent/bg_tasks.py、llm/usage_store.py、llm/anthropic.py、mcp/auth.py、hooks/trust.py、context/goal.py、runtime/cron.py、observability.py；修改集中在 repl.py/cli.py/executor.py/builtins.py/config.py/subagent.py/openai_compatible.py/factory.py/session/store.py/file_ops.py/slash_commands.py/bootstrap.py；README/TUTORIAL/docs 九文件同步；依赖 +pypdf/+opentelemetry（可选组，lock 同步）
- 行为成效：28 项存量台账全量清偿（24 任务交付 + 4 项处置结案）；测试 338→490（+152）；六项平台能力回归线全程绿色
- 构建：`uv sync --extra dev --frozen` 退出码 0（Checked 51 packages）；`uv run nexuscli --help` exit 0；`uv run nexuscli --version` → nexuscli 0.1.0
- 测试：2026-09-29 收尾实测：`uv run python -m pytest` → 490 passed in 27.87s（退出码 0）；`uv run ruff check .` → All checks passed!；`uv run ruff format --check .` → 141 files already formatted
- 手工验证：Mimosa security_scan（depth=deep）完成：findingCount=0、封印 sha256:49d7859a281621c7c9c8771e7962bfeeb7f7d2aba78df41cf777e52a066a8df3、scanId=scan-2026-09-29T10-47-38.288Z-6de6c0aba849；REPL 真机 smoke 维持 external_blocked（owner 用户，需真实 TTY）；OTel/Anthropic/MCP OAuth 按 spec 2.2 假设为 mock 口径

---

**验收结果**：通过
