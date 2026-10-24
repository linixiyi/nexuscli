# 轮次摘要：2026-10-25_add-workbench-mvp（master F2-F5）

## 交付
- 服务端（零新增 Python 依赖）：SSE 流（回放/实时/心跳/断开/16 槽位/api_key 查询参数仅 stream 路径）、
  --workbench 静态托管（穿越防护/SPA 回退/空态页/Content-Type 表）、三个读端点
  （/v1/sessions、transcript、/v1/usage/summary = UsageStore.stats 原样）
- 前端：frontend/ Vite+React19+TS strict，四视图（会话/回放/任务/usage）+ useEventStream
  （since 游标手工重连 + 降级轮询），dist 入库
- 纯重构：_build_http_server 提取、Origin 判定提至 net_guards.py（MCP 共用，行为零变化）

## 证据
- runs.jsonl：task_0001-0007 机器验证（含 npm ci && npm run build + dist in sync）
- 全量回归 1083 passed + 2 skipped（新增 22 用例）；ruff/format 绿
- 真实进程 smoke：首页托管真实 dist、资产 200 text/javascript、reads 返回真实本机会话与 usage、
  SSE 即时回放 thread.created、无 key 401

## 简化决策（对照 spec §4 暂不引入清单）
- 无 FastAPI/前端框架库/图表库；APPROVAL_TIMEOUT 类常量未配置化；审批挂起不在本轮（F6 属第 3 轮）
