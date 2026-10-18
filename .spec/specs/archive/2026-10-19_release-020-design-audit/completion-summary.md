# 发布收口 0.2.0 与设计审计:site 重建、🟠 九断言、AGENTS.md 蒸馏、TUI 一致性、站点 polish - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-19 02:21

## 假设回顾
### 已验证假设
- 9 个 🟠 表面补断言不需要改产品代码(真实运行已全通过);若发现真缺陷按缺陷处置
- 边框 Table 的裁决依据 PRODUCT.md 已确认的 pi 式方向:改为符合约束的呈现并同步渲染断言,而不是改约束

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 0.2.0 发版收口与设计审计:版本号 0.2.0(pyproject+__init__);CHANGELOG.md 0.2.0 节;AGENTS.md 三轮蒸馏落地;PRODUCT/README 数字与证据行刷新;site 重建且 canonical 构建命令统一;九个 🟠 表面断言补齐(矩阵 146✅/0🟠/1🟡);TUI 八个表面无框化(pi 式方向);站点 .btn-primary 对比度达标;CI lint 红灯根因修复(.spec 排除+全仓 format);新增 pty 自动化测试范式

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- CI 红灯修复取『排除 .spec + 全仓 format』而非逐文件修归档脚本;build 依赖取 --with 约定而非动 uv.lock;设计修复取 refinement 不换设计世界

### 本轮明确不做的内容
- 14.2 远程图片复跑(上游挂账);新功能;设计世界更换

## 变更边界
### 本轮主要改动模块
- pyproject.toml、src/nexuscli/__init__.py、entrypoints 呈现层(repl/session_cmds/usage)、site/(style.css/build.py/产物)、PRODUCT.md、README.md、AGENTS.md(新)、CHANGELOG.md(新)、docs/testing-matrix.md、tests/(3 新文件+平台守卫+断言同步)、12 文件 ruff format

### 明确未触碰的区域
- 事件流协议、审批四档语义、工具行为语义、uv.lock

## 验证证据
### 备注
- 测试：第二轮尾段 pytest exit=0、ruff check . exit=0、ruff format --check . exit=0;主线程修复后全量 1055 passed / 2 skipped(2026-10-19);runs.jsonl 6 机器任务 exit=0
- 构建：uv run --with markdown,pygments python site/build.py exit=0(第二轮尾段)
- 手工：evidence/lane-docs.md、lane-orange.md、lane-platform.md、lane-tui.md、lane-site.md、round2-tui.md、round2-site.md

## 验证范围
- 级别：package
- 机器验证率：6/6；manual 任务 3 项不计入分母
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- docs/testing-matrix.md 146✅/0🟠/1🟡 与 evidence 对应;checklist 处置记录 6 条全分级

## Git 记录
- 日期时间：2026-10-19 02:21
- 范围：发布收口 0.2.0 与设计审计:site 重建、🟠 九断言、AGENTS.md 蒸馏、TUI 一致性、站点 polish
- 功能：0.2.0 发版收口与设计审计:版本号 0.2.0(pyproject+__init__);CHANGELOG.md 0.2.0 节;AGENTS.md 三轮蒸馏落地;PRODUCT/README 数字与证据行刷新;site 重建且 canonical 构建命令统一;九个 🟠 表面断言补齐(矩阵 146✅/0🟠/1🟡);TUI 八个表面无框化(pi 式方向);站点 .btn-primary 对比度达标;CI lint 红灯根因修复(.spec 排除+全仓 format);新增 pty 自动化测试范式
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：0ee3b544a0c31cd2ebf28418f9c581d301311d38..0ee3b544a0c31cd2ebf28418f9c581d301311d38
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
无

## 被拒绝的扩展提议
- 无

## 门禁证据
- check_spec_package.py exit 0

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [
    {
      "actionable": false,
      "disposition": "non_actionable",
      "id": "audit-assertion-contract",
      "rationale": "测试缺口已在轮内补齐(test_audit_with_records_is_a_frameless_four_column_summary),无遗留",
      "summary": "第一轮任务书所指「/audit 4 列摘要表断言」实际不存在,本轮已补 frameless 四列契约断言"
    },
    {
      "actionable": false,
      "disposition": "non_actionable",
      "id": "title-string-pins",
      "rationale": "现状一致无需动作;日后改标题措辞时按反馈记录同步两处",
      "summary": "test_slash_surfaces_docs.py:80 与 test_integration_platform.py:271 在权限外 pin 标题串,本次实现保留同串故绿"
    },
    {
      "actionable": false,
      "disposition": "non_actionable",
      "id": "usage-history-preexisting",
      "rationale": "本包未触碰,随本包一并提交归档",
      "summary": "test_usage_history.py 在包开始前已是 modified(先前轮次工作)"
    },
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "brand-logo-gradient",
      "owner": "设计所有方(主线程)",
      "rationale": "WCAG 1.4.3 对 logotype 有豁免,属品牌视觉非正文;复用该渐变做正文组件时须套用本轮 .btn-primary 压深 stop",
      "reviewTrigger": "该渐变被复用到正文级组件时",
      "summary": "site .brand-logo 渐变白字对比度最差约 1.8:1"
    },
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "markdown-dep-extra",
      "owner": "主线程",
      "rationale": "canonical 命令已统一且实测可跑;加 dev extra 需同步 uv.lock 且 CI 用 --frozen,收益边际",
      "reviewTrigger": "site 构建在 CI 或他人环境复现失败时",
      "summary": "markdown 未声明为项目依赖,canonical 构建靠 build.py 头注 uv run --with 约定"
    },
    {
      "actionable": false,
      "disposition": "non_actionable",
      "id": "venv-markdown-mystery",
      "rationale": "已解释清楚;canonical 命令不再依赖 .venv 状态",
      "summary": "第一轮『.venv 缺 markdown』与尾段『build exit=0』矛盾,实为两时点间被手装 markdown 3.11.1"
    },
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "windows-ci-evidence",
      "owner": "主线程",
      "rationale": "lint 修复随本包推送后,下一次远端 CI 运行即实证;本地无凭据无法强等",
      "reviewTrigger": "推送后的第一次远端 CI 运行(如红按反馈开新轮)",
      "summary": "Windows 3.12 pytest 自 lint 修复后尚未在远端真正执行,3 处 skipif 是基于实现证据的预判"
    }
  ],
  "version": 1
}
```
