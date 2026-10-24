# 工作台 MVP（F2-F5） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-25 05:46

## 假设回顾
### 已验证假设
- frontend/dist 构建产物提交后，`serve --workbench` 开箱即用；Node 仅自行构建时需要
- MVP 无前端单测（vitest 不进本轮）：验收 = 构建通过 + Python 侧端点测试 + 真实进程 smoke

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 纯重构：提取 `_build_http_server`（port 注入）与 Origin loopback 判定共用函数
- SSE 事件流端点 GET /v1/threads/{id}/events/stream
- 静态托管 + serve --workbench 开关 + 空态页
- F4/F5 服务端读端点：/v1/sessions、/v1/sessions/{id}/transcript、/v1/usage/summary
- frontend/ 工程脚手架与 dist 入库
- 前端视图：外壳 + 会话/回放/任务/usage
- 全量回归 + 真实进程 smoke
- 计划同步：勾选 master F2-F5 + .spec/autorun/ 摘要

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：服务端全部用 stdlib 在 api.py 上叠端点；前端 Vite react-ts 最小依赖手写脚手架（不用交互式 create）
- 暂不引入：FastAPI/uvicorn/websockets、前端路由库/状态库/UI 组件库/图表库
- 不做的抽象/配置化：APPROVAL_TIMEOUT/MAX_SSE_CLIENTS 保持模块常量（01 挂账④）；审批挂起不进本轮

### 本轮明确不做的内容
- F6/F7（Web 审批卡片、Web 发起 turn——第 3 轮）；F8（CI 与文档收尾——第 4 轮）；F9-F13
- WebSocket、多用户认证、公网部署；dist 打进 wheel（挂账）
- 前端单元测试框架（vitest 挂 F8 之后）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- F6/F7（Web 审批卡片、Web 发起 turn——第 3 轮）；F8（CI 与文档收尾——第 4 轮）；F9-F13
- WebSocket、多用户认证、公网部署；dist 打进 wheel（挂账）
- 前端单元测试框架（vitest 挂 F8 之后）

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：8/8
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-25 05:46
- 范围：工作台 MVP（F2-F5）
- 功能：纯重构：提取 `_build_http_server`（port 注入）与 Origin loopback 判定共用函数；SSE 事件流端点 GET /v1/threads/{id}/events/stream；静态托管 + serve --workbench 开关 + 空态页；F4/F5 服务端读端点：/v1/sessions、/v1/sessions/{id}/transcript、/v1/usage/summary；frontend/ 工程脚手架与 dist 入库；前端视图：外壳 + 会话/回放/任务/usage；全量回归 + 真实进程 smoke；计划同步：勾选 master F2-F5 + .spec/autorun/ 摘要
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：48aad7956ca0b5cbbcb1436c60fae49510e0bdfb..93b1626187b1021c47d735582ad341fc75d85e46
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
- SSE 流式响应在标准库 http.server 上可行且无需 asyncio：每连接一线程 + Condition 唤醒 + BoundedSemaphore 上限；测试侧 http.client 对 close-delimited 响应 getresponse() 后 conn.sock 置 None，强制收口要用看门狗线程对 response.fp.raw._sock.shutdown（socket 超时会使 buffered reader 中毒，不可复用）。
```

## 被拒绝的扩展提议
- 无

## 门禁证据
- check_spec_package.py exit 0

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [],
  "version": 1
}
```
