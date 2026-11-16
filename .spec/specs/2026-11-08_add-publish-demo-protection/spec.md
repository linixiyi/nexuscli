# NexusCLI 发布收尾：PyPI 发布、demo 资产与分支保护 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义

- **项目目标**：承接已归档包 2026-11-07_fix-nexuscli-release-hardening 偏离日志 D3 拆出的三项 owner 手动任务（原 task_0006/0008/0009），boundary/verify 原文逐字保留；执行者必须具备对应权限与本地工具。
- **目标用户**：NexusCLI 维护者（owner）。
- **核心价值**：把 AI 无法代填的发布收尾动作纳入 spec 流程记录，保持可追溯与验收闭环。
- **Literal ask**: 「done然后push」（2026-11-08，用户指令本轮归档并合入 main）；三项任务源自 m00459 的范围列举。
- **Stated purpose**: 发布 0.2.1 到 PyPI、补 demo 资产、启用 main 分支保护。
- **Inferred outcome**: owner 完成三项后，nexuscli 0.2.1 在 PyPI 可安装、README 有 demo 资产、main 受分支保护；confidence: high; evidence: 前置包 tasks.md 原文与归档记录。
- **Constraints and exclusions**: 三项均不改源码；AI 不代填；发布依赖 owner 的 PyPI trusted publisher 配置与 GitHub 管理员权限。
- **Trade-off preference**: 完整性与可追溯优先于速度。

## 2. 假设与待确认

### 2.1 已确认事实

- 前置包 2026-11-07_fix-nexuscli-release-hardening 已归档并合入 main：tag 触发的 release workflow、README/社区文件、CI 门禁（lint/test/eval/frontend）均已就绪。
- 0.2.1 版本号已写入 `pyproject.toml` 与 `src/nexuscli/__init__.py`；`scripts/check_release_artifacts.py` 已入 release workflow 把关产物内容。

### 2.3 待确认问题

- [x] 三项为何独立成包？→ 2026-11-08 前置包偏离 D3：三项均需 owner 凭据/管理员权限/本地录制，AI 不可代填；前置包需收敛归档以完成 done+push。

## 3. 功能范围

### 3.1 核心功能（MVP）

- 发布 0.2.1 到 PyPI 并验证安装（原 task_0006）。
- 补 20 秒 demo GIF 与 workbench 截图（原 task_0008）。
- 给 main 配 required checks 与 PR 门禁（原 task_0009）。

### 3.3 不在范围内

- 不改任何源码；三项全部为仓库设置、凭据操作与本地录制。

## 5. 技术决策

- Git integration branch：`spec/2026-11-08_add-publish-demo-protection`

### 5.1 验证策略

- 范围级别：package
- 变更对象：无源码变更；仅仓库设置、tag 与 demo 资产。
- 快速检查：`git tag -l "v0.2.1"`；`grep -c "TODO(截图)" README.md`（owner 完成对应任务后分别应为非空输出与 0）
- 集成检查：`git ls-remote --tags origin refs/tags/v0.2.1`（PyPI 侧以干净环境 `pip install nexuscli==0.2.1` 输出为准）
- 全项目检查：适用外（三项均为 manual verify；owner 执行后按各任务记录执行人、命令、结果并勾选）。
- 升级触发：owner 完成全部三项并勾选后归档本包；无机器 verify 可升级。

<!-- spec:correction-log -->
### 5.5 修正日志

- none.

<!-- spec:deviation-log -->
### 5.6 偏离日志

- none.

## 6. 成功标准与验证方式

- [ ] 0.2.1 已发布到 PyPI，干净环境 `pip install nexuscli==0.2.1` 后 `nexuscli --version` 正确 -> verify: task_0001
- [ ] README 首屏有 20 秒 demo GIF 与 workbench 截图，`TODO(截图)` 已移除 -> verify: task_0002
- [ ] main 分支启用 PR + required checks，直接 push 被拒绝 -> verify: task_0003

## 7. 风险与约束

- 风险点：推送 `v0.2.1` tag 前必须先在 PyPI 配置 trusted publisher 与 `pypi` environment，否则 release workflow 会因无凭据失败。→ 缓解：0001 verify 明确「先配置、后推 tag」的顺序。
- 风险点：README 行号可能随前置包改写而变化。→ 缓解：owner 按 `TODO(截图)` 文本搜索定位，不依赖行号。
