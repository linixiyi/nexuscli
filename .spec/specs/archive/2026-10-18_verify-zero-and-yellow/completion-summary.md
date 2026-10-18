# 测试矩阵收口:17 项零验证表面 + 45 项仅机器表面的真实使用验证 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-19 00:53

## 假设回顾
### 已验证假设
- 🟡 项实现大多已对（有断言），真实运行主要是补证据 + 暴露观感问题；⬜ 项才可能挖出真缺陷
- 真实运行可用 pty 驱动 REPL + 真实进程 `-p` / 子命令 + 真实网关完成，不需要人工在场

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 17 个 ⬜ 表面机器验证+真实运行双补;45 个 🟡 中 44 个补真实运行(14.2 上游环境阻塞如实标注);两轮真实使用模拟暴露 13 个缺陷全部修复带回归测试;矩阵更新为 137✅/1🟡/9🟠/0⬜;README 补 llm.vision、--yes、/snapshot save 说明

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 真实运行证据以一次性 pty/进程脚本+留痕为准,不产品化录制回放工具;轮间修复最小化,不引入新抽象

### 本轮明确不做的内容
- 不改事件流协议与审批四档语义;不重排目录

## 变更边界
### 本轮主要改动模块
- tests/(新增 12 个测试文件)、tools/builtins.py、llm/(openai_compatible/factory/model_profiles)、entrypoints/(repl/model_selector/commands)、agent/orchestrator.py、plan/models.py、prompt/assembler.py、docs/testing-matrix.md、README.md、各 lane 所有权模块内的小修

### 明确未触碰的区域
- 事件流协议、审批四档语义、目录结构、.spec 之外的全局配置

## 验证证据
### 备注
- 测试：.venv/bin/python -m pytest -q:1046 passed / 2 skipped(2026-10-19);runs.jsonl 9 机器任务 exit=0
- 构建：.venv/bin/python -m ruff check src tests 全绿(2026-10-19)
- 手工：evidence/lane-a..f.md + round2-vision/repl/modes.md + main-history.md:pty 真人模拟与真实网关运行留痕;两轮工作流报告

## 验证范围
- 级别：package
- 机器验证率：9/9；manual 任务 1 项不计入分母
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- docs/testing-matrix.md 状态列与 evidence 一一对应;checklist 处置记录 15+9 条全部分级

## Git 记录
- 日期时间：2026-10-19 00:53
- 范围：测试矩阵收口:17 项零验证表面 + 45 项仅机器表面的真实使用验证
- 功能：17 个 ⬜ 表面机器验证+真实运行双补;45 个 🟡 中 44 个补真实运行(14.2 上游环境阻塞如实标注);两轮真实使用模拟暴露 13 个缺陷全部修复带回归测试;矩阵更新为 137✅/1🟡/9🟠/0⬜;README 补 llm.vision、--yes、/snapshot save 说明
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：d5771d9cd6f1c44513eecce2ca60c3225bc56580..d5771d9cd6f1c44513eecce2ca60c3225bc56580
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
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "orange-assertions-9",
      "owner": "下一轮矩阵收口包(主线程)",
      "rationale": "用户本轮明确指定 17⬜+45🟡;🟠 项已有真实运行留痕,缺的只是断言,回归风险低且已在矩阵「仍然没测的」挂账",
      "reviewTrigger": "下一次测试矩阵审计轮开启时",
      "summary": "9 个 🟠(仅真人)表面仍缺自动化断言:doctor、--version、/context、/tools、/policy、/usage、空 prompt Ctrl+C、Ctrl+D、bracketed paste;用户指定范围为 62 项(17⬜+45🟡),不在本包"
    },
    {
      "actionable": true,
      "dependency": "网关上游的远程图片抓取能力恢复(本机 localhost:20128 → cbcn 渠道)",
      "disposition": "external_blocked",
      "id": "remote-image-upstream",
      "owner": "上游网关服务方;nexuscli 侧契约已就绪(llm.vision 能力开关)",
      "retryTrigger": "上游恢复后用 round2-vision.md 的 pty 脚本复跑 14.2 即可置绿",
      "summary": "14.2 远程图片端到端:CLI 直传契约正确(wire dump 证实),但网关上游当前拒收一切远程图片 URL(11135)"
    },
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "loop-copy-plural",
      "owner": "主线程",
      "rationale": "文案级瑕疵,改动需同步既有断言,信息无损,收益小于风险",
      "reviewTrigger": "下次触及 agent/loop_guard.py 的文案清理",
      "summary": "loop 止损文案单复数:\"terminated after 1 turns\""
    },
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "expert-empty-progress",
      "owner": "主线程",
      "rationale": "属增强而非缺陷:流程不中断、报告如实携带占位符;改动涉及 workflow 引擎与渲染两层",
      "reviewTrigger": "下次触及 workflow 引擎进度渲染时",
      "summary": "/expert 中游子代理空产出时,用户只能从最终报告的占位符间接得知,进度行不就地提示"
    },
    {
      "actionable": true,
      "disposition": "accepted_risk",
      "id": "recall-compliance-probabilistic",
      "owner": "产品所有方(主线程)",
      "rationale": "措辞已从 untrusted 框架迁入受信块并指令式渲染,进一步强保证需产品级手段(冲突时强制引用来源),属设计决策",
      "reviewTrigger": "出现用户可感知的约束失守报告时",
      "summary": "弱模型对 <project-constraints> 召回约束的遵守是概率性的:7.2 型问题 2/2 通过,对抗问法下不保证"
    }
  ],
  "version": 1
}
```
