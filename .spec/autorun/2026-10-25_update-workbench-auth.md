# 轮次摘要：2026-10-25_update-workbench-auth（F14/F15，用户直接指令按 kitagent 模型）

## 交付
- F14：runtime/websession.py（bootstrap_token/cookie/csrf 三秘密、一次性 bootstrap、
  authorize、csrf_matches）；POST /v1/workbench/bootstrap（Set-Cookie HttpOnly
  SameSite=strict + JSON csrf，重放/错口令 403）；_handle 门重构（静态免门 → bootstrap
  免门 → Origin 白名单先于凭证 → 三通道凭证 api_key 头/api_key 查询参数/cookie →
  cookie 通道 POST 强制 x-csrf-token）；启动打印一键链接
- F15：前端删 key 门（localStorage 凭证废弃）；#bootstrap 片段自动换发（换发后抹地址栏）；
  csrf 内存态 + POST 附 x-csrf-token；SSE 走 cookie 同源携带（api_key 查询参数仅外部保留）

## 证据
- runs.jsonl：task_0001-0005 机器验证；全量回归 1138 passed；ruff/format 绿
- 既有 api_key 外部通道测试零适配通过（通道兼容性实测）；7 个新鉴权用例

## 简化决策
- 单会话（进程生命周期即会话生命周期，kitagent 同款）；无 cookie 轮换/持久化；
  CSP 响应头挂账；docs/runtime-api.md 外部契约不变
