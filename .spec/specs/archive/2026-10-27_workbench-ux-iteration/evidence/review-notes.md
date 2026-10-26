# check 轮发现与处置（2026-10-27，独立评审员 6 条 + 门禁 2 失败 + 主会话核实）

## 门禁失败 2 条（工作流终态）
- ruff format api.py：**resolved_current**（主会话 ruff format 修复，--check 全绿）。
- e2e nonzero：**resolved_current**（复跑 8/8 全绿，含 U1/U2 回归；工作流终态那次
  属时序 flake，list reporter 复跑无 skip）。

## R1（中）多标签页下单值 csrf 轮换乒乓
- 评审员报 unconfirmed；主会话核实属实：第二标签页自动重签会顶掉第一标签页的
  令牌，旧标签页 403 无自愈。
- 处置：**resolved_current**：client.ts request() 对 403 且 error 含 "csrf" 的 POST
  自动重签一次并重试一次（reissueCsrf 带在飞护栏；bootstrap 403 文案不含 csrf
  不受影响）——乒乓收敛为「每次请求至多一次自愈」，last-writer-wins 仍成立但
  双标签页均可继续工作。build + e2e 复跑绿。

## R2（中）after/ 证据缺失
- **resolved_current**：全部代码收尾后重跑 journey（scripts/ui_journey.py --out
  evidence/after），18 张落库；after/02（U1 错误呈现+表单可重试）与 after/11
  （刷新后可写）人工目检通过。

## R3（低）journey step10 reload 偶发 30s 超时
- **resolved_current**：runner 已带 reloadWithRetry（实现者落地，双 reload 点均走
  重试）；本轮复跑未再复现。

## R4（低）findings.md「19 张」与 18 张实物不符
- **resolved_current**：勘误为 18 张并注明 09 为条件步骤。

## R5（低）U2 e2e 止于发送按钮可用
- **resolved_current**：实现者在评审快照后已扩展用例（现名「…且重签 csrf 能真发
  变更请求」），主会话复跑 8/8 绿确认；服务端半边由
  test_reissue_with_cookie_rotates_csrf 的真实 POST /v1/threads 覆盖。

## R6（低）journey 入库形态 py+mjs 两文件 vs spec 字面 ui_journey.mjs
- **resolved_current**：按实现形态记账（py 管服务生命周期符合 AGENTS.md 进程组
  纪律，mjs 管 Playwright 步骤）；spec §5 字面在此勘误。

## 附带发现
- docs/runtime-api.md:110 孤立行「drift line」（25d9576 引入，无引用）：
  **resolved_followup**（不动本轮边界，留给 owner 随下次文档轮清理）。
