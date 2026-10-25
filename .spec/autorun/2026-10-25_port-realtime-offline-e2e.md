# 轮次摘要：2026-10-25_port-realtime-offline-e2e（对齐 A10-A12）

## 交付（/workflow 三 lane 并行照搬，出处注释保留）
- A10（api.py）：审批挂起/解决入 SSE（轮询对比快照 diff + 收口点登记台账，解决帧近零延迟；
  只推送本线程 scope 卡）+ 50ms token 合帧（推送侧合并、回放段逐行原文保 A7 契约）+
  积压 resync 帧（高水位 256）；12 SSE 用例
- A11（llm/offline_demo.py + factory 注入缝 + scripts/web_validation_server.py）：
  离线确定性 provider（脚本链 read→write 审批门→read 回验，逐 8 字符流式，无网络）+
  e2e 启动器（READY 帧 stdio 交接口令、禁入日志）；5 用例 + 真服务烟雾全链跑通
- A12（frontend state.ts/useWorkbench.ts/e2e/）：seq-patch 应用器（断档 SequenceGap→
  重拉快照）+ pending 幂等恢复（nexuscli:pending）+ Playwright 5 条基线；e2e 实跑
  3 passed / 1 failed / 1 依赖跳过

## 挂账（如实）
- e2e「会话列表」用例失败（heading 未渲染，串行 authedPage 交接或 UI 回归，待交互式调试）；
  「任务面板」用例依赖其通过而未运行——A10/A11/A12 机制面各有单测覆盖，e2e 修复挂账
- 跨 lane 契约：A10 合帧帧无行级 seq，前端 state.ts 已在收口时加合帧旁路（按 SSE id 跳游标）
- 主会话收口修复三项跨 lane 缝隙：client.ts POST 漏 csrf 头（createThreadId 403 真 bug）、
  验证服务器不托管 dist（e2e skip 根因）、state.ts 合帧旁路

## 收口修复（主会话，跨 lane 契约缝隙三件 + e2e 根因）
- client.ts：POST 一律附 x-csrf-token（修复 createThreadId 无体 POST 漏头 → cookie 通道 403 的
  真 bug——现有「发送」按钮此前在真服务上不可用）
- state.ts：A10 合帧旁路（text_delta 合并帧无行级 seq，按 SSE id 跳游标，文本无丢失）
- scripts/web_validation_server.py：仓库 dist 同步进 demo 工作区（4 条 e2e skip 的根因）
- e2e 根因修复：authedPage 交接的是每测试隔离的 page fixture（用例结束即销毁）→ 改 browser
  fixture 持久 context + apiRequest 交接；修复后 **5/5 全绿**（含此前失败的会话列表用例——
  确认为测试编写缺陷，非产品回归）
- frontend/test-results/ 加入 frontend/.gitignore
