# 工作台鉴权重构：一次性口令 + Cookie 会话（2026-10-25，用户直接指令）

## 目标

消除工作台的 key 手输环节：按 kitagent web/gateway.py 的成熟模型（/tmp/kitagent-src/
src/agent_runtime/web/{app,gateway}.py 实读定稿），改为「一次性启动口令 + HttpOnly Cookie
会话 + CSRF + Origin 白名单」。用户点服务启动时打印的链接即完成认证，零输入。

## 已确认事实

- kitagent 模型（实读）：启动生成 bootstrap_token/cookie/csrf 三秘密并打印 #bootstrap=token
  链接；POST /api/bootstrap 一次性换发 HttpOnly SameSite=strict cookie（JSON 返回 csrf）；
  之后 authorize=cookie 比对 + x-csrf-token 头 + Origin 白名单三重；静态资源免认证；带 CSP 头
- 现状：/v1/* 只认 x-api-key/Bearer 头；浏览器侧靠手输 key 存 localStorage（用户判定不合理，
  本轮直接指令按 kitagent 模型重构）；SSE 另有 api_key 查询参数（EventSource 带不了头）
- 会话 JSONL/usage 等读端点含本机敏感数据，重构不得降低防护（Cookie+CSRF+Origin 三重
  对浏览器面的防护强度不低于单一 key）

## 特性

- [ ] F14 服务端：bootstrap 端点（一次性口令换发 HttpOnly SameSite=strict cookie + csrf）+
  /v1/* 会话门（cookie 或既有 api_key 头二选一）+ 变更请求 CSRF 校验 + /v1/* 全面 Origin 白名单 +
  启动打印一键链接；api_key 头保留为外部自动化通道
- [ ] F15 前端：key 门删除；加载时读 #bootstrap= 片段 → POST bootstrap → csrf 存内存；
  变更请求附 x-csrf-token；SSE/EventSource 走 cookie 同源自动携带

## 边界

- 不做用户体系/多会话 cookie（单会话，进程生命周期即会话生命周期——kitagent 同款）
- 不做 token 持久化与刷新（重启即新一轮口令；kitagent 同款 bootstrapped 一次性语义）
- api_key 头与 SSE 的 api_key 查询参数保留（外部自动化契约，docs/runtime-api.md 不破坏）

## 验证口径

- bootstrap 一次性语义（重放 403）、cookie 授权、CSRF 强制（变更请求缺头 403）、
  Origin 白名单（跨源 403、本机放行）、api_key 外部通道回归、前端构建通过
