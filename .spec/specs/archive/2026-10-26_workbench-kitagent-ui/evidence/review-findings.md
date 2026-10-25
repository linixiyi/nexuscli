# check 轮独立复审发现与处置（2026-10-26，reviewer sidecar 终局换眼）

评审者：只读 sidecar（全新上下文，未参与实现）。原文返回 7 条发现；主会话逐条裁决，
可执行项本轮修复完毕。风险路由「认证/信任边界」。

## F1（高）state.ts 合帧旁路把全局行 id 写进 per-thread seq 游标 → 真实环境丢事件
- 处置：**resolved_current**。applyEvent 合帧分支不再触碰 seq 游标（全局行 id 与
  per-thread seq 是两套计数，api.py:1114-1117）。用评审者给的四帧序列复现验证：
  修复前 seq=105、tool_call/turn.completed 全丢；修复后 seq=4、5 个事件齐全。
- 教训（进 AGENTS.md 增量提议）：demo/e2e 的全新 HOME + 首线程使全局行 id 恰好等于
  per-thread seq，精确掩蔽了这类游标混用 bug——涉及两套计数器的代码必须有跨线程
  场景的测试。

## F2（高）gate 粘贴换发成功后落「只读态」且口令已烧（reload 丢 csrf）
- 处置：**resolved_current**。useWorkbench 新增 redeem()（页内换发+probe+pending 恢复，
  绝不 reload）；App Gate 改走 redeem。e2e 新增「粘贴换发成功后发送按钮可用」回归
  用例（独立服务实例，不烧主 token），6/6 绿。

## F3（中）审批决策错误被吞 + 有效期/五态缺 + 跨线程卡混显
- 错误吞掉 → **resolved_current**：非 404 失败保留卡片并就地呈现「决策未送达」，
  404（已解决/过期）才移除；卡片补「挂起于」时间行。
- 有效期字段 → **resolved_followup**：后端 payload 无 expires_at，本轮边界禁改
  API 响应形状；挂账为后续小包（approvals.py as_payload 加 expires_at + 前端展示）。
- 五态展示 → **non_actionable**：本仓工作台是 poll-pending-only 模型（GET /v1/approvals
  只返回 pending），resolved/expired 卡片本就不出现；gaps.md G12 的「五态」表述系
  照搬 kitagent WS 模型的过度承诺，按轮询模型修正口径。
- 跨线程卡混显 → **accepted_risk**：保留全部 pending + scope 标注（用户级审批必须
  任何视图可达，否则其他线程的审批无处可批）；标注格式已带 scope 前缀。

## F4（中低）e2e 证据时长可疑（疑似 skip 充通过）
- 处置：**resolved_current**。--reporter=list 复跑：6 条全 passed、0 skipped、3.0s
  总时长（本机 chromium 常驻 + demo provider 快）；用例级断言（turn 文本含「检查报告」、
  seq 连续、幂等重放同回执）均为真实执行。list 输出已入本轮 runs.jsonl 对应任务的
  verify 记录。

## F5（低）畸形 hash（#/threads/%zz）URIError 白屏
- 处置：**resolved_current**。readRoute 与 useWorkbench 初始解析均 try/catch 回退原文。

## F6（低）文档/边界漂移
- ① useWorkbench 头注「暂未接入 App.tsx」→ **resolved_current**（已更新）。
- ② tasks.md boundary 列的 SessionsView/TranscriptView/timeline.ts 实际被删除 →
  **resolved_current**（本文件与 tasks.md 末尾备注回写；功能并入
  Sidebar/ChatPanel/Inspector/ReplayView/derive）。
- ③ report.ts 注释指向已删文件 → **resolved_current**。
- ④ spec §6 双层复选框 → **resolved_current**（归一为单层；系勾选脚本误写）。

## F7（低）链接文件 write-then-chmod 弱权限窗口
- 处置：**resolved_current**。改 os.open(O_CREAT|O_TRUNC, 0o600) 直接以 0600 创建；
  tests/test_runtime_hardening.py 的 0600 断言继续覆盖。
- 多实例并发覆盖前者链接 → **accepted_risk**（docstring 已认「每次启动覆盖」；
  跨实例场景前者只剩启动日志可查，与 kitagent 语义一致）。
