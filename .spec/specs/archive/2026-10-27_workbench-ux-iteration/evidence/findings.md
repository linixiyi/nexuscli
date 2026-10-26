# UX 实测发现清单（2026-10-27，PM/真实用户视角）

方法：web_validation_server（离线 demo）真实起服 → Playwright 走完整用户旅程
（18 张截图，evidence/baseline/；步骤编号到 19，但 09-tool-detail 是条件步骤——
tool-row 不可见即跳过，两轮实跑均产出 18 张，无 09 号）→ 逐张以「第一次用的真人」
标准审看。零页面错误。

## P0（阻断真实使用）

- **U1 坏口令卡死**（baseline/02-gate-bad-token.png）：gate 粘贴错口令后整页只剩
  「正在完成一次性认证…」，无错误信息、无重试入口。根因：useWorkbench.redeem
  失败路径不还原 auth phase（停在 bootstrapping，Gate 渲染成进行中分支）。
- **U2 刷新后只读死局**（11-reload-restore / 14 / 19）：F5 后回放完整恢复，但
  composer 变「当前页面为只读态（无 CSRF 凭证），请用一键链接重新进入」——而口令
  一次性已烧掉，链接文件里的也是死链。用户刷新一次 = 永久失去发送/审批能力，
  唯一出路是重启服务。根因：csrf 仅存内存（F15 有意）+ 无「凭有效会话 cookie
  重签 csrf」通道。安全性分析：向已持有效会话 cookie 的同源客户端重签 csrf 不降低
  防线（csrf 防跨源伪造；持 cookie 的同源 JS 本就能发请求），kitagent 语义中
  csrf 本就是会话级衍生秘密。

## P1（明显摩擦）

- **U3 线程不存在无告知**（14-nonexistent-thread.png）：手输/残留 hash 指向不存在
  的线程时，页面安静地显示欢迎页+检查器空态，唯一线索是状态栏小字「连接断开」。
  应有显著错误条（useWorkbench 已产 streamError「线程不存在或已随实例重置」，
  只是没有呈现位）。
- **U4 审批等待期误报「执行中」**（06/07）：挂起等审批时聊天面板 run-inline 显示
  「执行中」，行动指令应改为「等待审批 · 请在右侧面板批准」（kitagent 有 approval
  态区分，本仓 busy 一刀切）。
- **U5 时间戳 locale 泄漏**（07）：审批卡「挂起于 10/26/2026, 5:37:13 PM」英文格式
  ——toLocaleString() 未显式 zh-CN，跟随浏览器默认。

## P2（打磨）

- **U7 实现细节当用户文案**（16/17）：任务面板「每 2s 轮询 GET /v1/tasks；取消走
  POST /v1/tasks/{id}/cancel」、用量面板「GET /v1/usage/summary（= UsageStore.stats
  原样）…」——端点与内部结构不该出现在产品文案；改为用户视角描述（e2e 断言同步）。

## 观察为合格（不改）

- 欢迎页/示例任务（03/04）、流式运行指示（05/06）、审批卡信息结构（07）、完成态
  （08）、报告面板与导出真实下载（10，downloaded 实证）、侧栏会话切换（12/13）、
  900px 平板与 500px 手机布局（18/19）、畸形 hash 不白屏（15）。
- U6（报告「执行事实」仅一行聚合，弱于 kitagent 逐条证据列表）与 U8（离线 demo
  无 DEMO 横幅）挂账后续，不进本轮。

## 修复后复跑对照（evidence/after/，2026-10-27）

命令：`uv run python scripts/ui_journey.py --out .spec/specs/2026-10-27_workbench-ux-iteration/evidence/after`
→ exit 0、18 张（同 baseline 口径，09 条件步骤跳过）、pageerrors: none、export:
downloaded、11-reload-restore 一次通过。

- **U1**（after/02-gate-bad-token ↔ baseline/02）：坏口令就地报错「口令无效或已被
  使用…」并给出取回链接的两条恢复路径说明，输入框保留可重试——不再卡
  「正在完成一次性认证…」。
- **U2**（after/11-reload-restore ↔ baseline/11）：刷新后 composer 回到默认占位符
  且发送键可用，检查器「SSE 已连接」、事件回放完整——只读死局消除。服务端语义：
  重签为**收敛**（返回当前 csrf 不轮换，多标签页互不影响；早期「重签即替换」会让
  第二标签页冷加载踢掉已打开标签页的写能力，属评审发现的多标签页回归）。
- **U3**（after/14-nonexistent-thread）：不存在的线程出现显著错误条「线程不存在或
  已随实例重置」。U5（after/07）：挂起时间为 zh-CN 格式。U7（after/16/17）：任务/
  用量副标题为用户文案「后台队列任务实时刷新，可在此取消」「近 7 天回合、Token 与
  成本汇总」。
- 附带修复（本轮评审发现 #3 根因）：useWorkbench SSE 重连链路存在僵尸流泄漏——
  resync 定时器与被 abort 流的 catch 双触发（丢句柄）→ 双 connect → 旧 controller
  引用被覆盖成不可 abort；每条泄漏流占死一个浏览器 per-host socket，6 条耗尽
  Chromium 连接池后页面任何导航（reload）永久排队。这正是 journey 首跑 step10
  reload 30s 超时、复跑随机的根因；修复（连接代际号 + 重设前清定时器）后连续
  3 轮 journey 全绿且 reload 重试一次都未触发。
