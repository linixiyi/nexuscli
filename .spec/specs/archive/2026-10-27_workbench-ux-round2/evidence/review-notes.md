# check 轮记录（2026-10-27，独立复核 5 条 + 门禁 1 失败）

- e2e 门禁 nonzero：**resolved_current**——主会话复跑 11/11 全绿（含三条新用例
  W1/U6/W2），工作流终态那次属时序 flake（与上轮同款 stack-tail，list 复跑无 skip）。
- 复核 4 条通过性核查（计时器泄漏/日期格式/跳转选中/边界）：**resolved_current**
  （复核员已实读逐条确认通过，无需改动；证据见其 notes 引文）。
- 复核行为观察「审批挂起期两条等待文案并存且措辞矛盾」：**resolved_current**——
  采纳，慢等待提示加 !waitingApproval 条件（阻塞源是用户决策不是模型），
  rebuild + e2e 11/11 + journey 复跑全绿。
- W2「条目小字含日期」未做（侧栏 220px 无省略号样式会溢出，index.css 不在允许
  清单）：**accepted_risk**——可辨识改由回退标题 + title 悬浮属性承担，达成
  spec §3.1 的可辨识目标；样式增强挂账。
