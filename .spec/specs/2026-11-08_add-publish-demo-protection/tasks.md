# NexusCLI 发布收尾 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- manual 任务必须由 owner 执行，并记录执行人、命令、结果；不能由 AI 代填。
- 三个任务承接自 2026-11-07_fix-nexuscli-release-hardening（已归档）偏离日志 D3，boundary/verify 原文逐字保留；原前置依赖（task-0005、task-0007、task-0001）已在该包完成并合入 main，不再重复声明。

## 阶段 A：发布收尾（owner 手动）

- [ ] 0001 发布 0.2.1 到 PyPI 并验证安装（原 task_0006）
  - boundary: 仅仓库设置、PyPI trusted publisher、tag 与发布操作；不改源码。
  - verify: manual: 需要 PyPI trusted publisher 与 tag 推送权限；配置 `pypi` environment 后推送 v0.2.1 tag，等待 release workflow 成功；在干净虚拟环境运行 pip install nexuscli==0.2.1 与 nexuscli --version，记录输出和 PyPI 文件列表。

- [ ] 0002 补 20 秒 demo GIF 与 workbench 截图（原 task_0008）
  - boundary: 新增 `docs/demo.gif`、新增 `docs/workbench.png`、`README.md`（替换 README 中的 `TODO(截图)`，在首屏/Workbench 节引用两张图）。
  - verify: manual: 需要真实终端录制与浏览器截图；用 asciinema+agg 或 vhs 录制 20 秒主流程，用 Playwright 截 workbench 审批界面；检查文件非空、README 无 TODO(截图)、图片在 GitHub 渲染正常。

- [ ] 0003 给 main 配 required checks 与 PR 门禁（原 task_0009）
  - boundary: 仅 GitHub 仓库设置；不改源码。
  - verify: manual: 需要仓库管理员权限；在 branch protection 中启用 Require a pull request、Require status checks（lint / test / frontend）、Dismiss stale approvals、Block force pushes；用 gh api 记录 branches/main/protection 结果或页面截图，并验证直接 push 被拒绝。

---

**当前进度**：由脚本计算，无需手动维护
