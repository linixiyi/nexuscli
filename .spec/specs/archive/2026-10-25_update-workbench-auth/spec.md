# 工作台鉴权重构（一次性口令 + Cookie 会话） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：消除工作台的 key 手输环节——按 kitagent web/gateway.py 实读模型改为
  「一次性启动口令换发 HttpOnly Cookie + CSRF + Origin 白名单」，用户点启动打印的链接即用
- **目标用户**：localhost 单人用户（浏览器零输入）；外部自动化（api_key 头通道保留）
- **核心价值**：体验（零输入）与防护（三重校验对浏览器面不低于单一 key）双升

## 2. 假设与待确认

### 2.1 已确认事实
- 契约来源：kitagent 源码实读（/tmp/kitagent-src web/app.py + gateway.py）+ 用户直接指令「按照他的来」
- 现状：/v1/* 只认 x-api-key/Bearer；SSE 另有 api_key 查询参数；前端 key 门存 localStorage
- 设计细节（kitagent 定稿口径）：bootstrap 一次性（bootstrapped 标志，重放 403）；cookie
  HttpOnly SameSite=strict；变更请求校验 x-csrf-token（compare_digest）；Origin 白名单
  = 本服务自身来源；静态资源免认证

### 2.2 关键假设
- 单会话语义（进程生命周期即会话生命周期，重启即新一轮口令）可接受——kitagent 同款
- api_key 头通道保留不破坏 docs/runtime-api.md 的外部接入契约与既有测试

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：Cookie 存内存态（非持久）-> 重启重新 bootstrap，换取无持久凭证文件
- 当前选择：SSE 保留 api_key 查询参数 -> 外部 curl/EventSource 通道兼容；浏览器走 cookie

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] F14 服务端：bootstrap 端点 + 会话门（cookie 或 api_key 二选一）+ 变更请求 CSRF + Origin 白名单全面化 + 启动打印一键链接
- [x] F15 前端：删 key 门；#bootstrap 片段自动换发；csrf 存内存；变更请求带 x-csrf-token

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 多用户/多会话 cookie；token 持久化与刷新；CSP 响应头（挂账，kitagent 有、本轮不做）；docs/runtime-api.md 面改动

## 4. 最小实现路径
- 最简单可行方案：新模块 runtime/websession.py（三秘密生成 + authorize）+ api.py 一个 bootstrap 端点 + _handle 门改造；前端 App 加载逻辑替换 key 门
- 暂不引入：cookie 轮换、多实例、持久会话
- 不做的抽象/配置化：口令长度/会话时长不做 config 项（模块常量）

## 5. 技术决策
- 技术栈：stdlib http.server 的 SimpleCookie/Set-Cookie + secrets.compare_digest
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/runtime/websession.py（新）、runtime/api.py（门与端点）、entrypoints/cli.py（打印链接）、frontend/src/（App/bootstrap/csrf）、tests/test_runtime_websession.py（新）、既有 runtime 测试的鉴权适配
- 本轮不应触碰：tools/、llm/、plan/、docs/ 正文（workbench.md 鉴权节同步属本轮验收必需，最小触碰）、.github/
- Git integration branch：`spec/2026-10-25_update-workbench-auth`

### 5.1 验证策略
- 范围级别：package
- 变更对象：Runtime API 鉴权面、前端加载流程
- 快速检查：`uv run python -m pytest -q tests/test_runtime_websession.py`
- 集成检查：`cd frontend && npm run build` + 既有 runtime/approvals/sse/static/reads 测试全绿（鉴权适配后）
- 全项目检查：适用外——与 CI 一致的三步回归由收口任务执行
- 升级触发：若既有 api_key 外部通道测试无法适配（契约被破坏）即升级并记录

## 6. 成功标准与验证方式
- [x] bootstrap 一次性语义：正确口令换发 Set-Cookie（HttpOnly/SameSite=strict）+ csrf；重放/错口令 403 -> verify: task_0001
- [x] 会话门：cookie 放行；无 cookie 无 key 401；api_key 头通道回归不破 -> verify: task_0002
- [x] CSRF：变更请求（decision/turns/tasks/threads）缺 x-csrf-token 403（api_key 通道豁免） -> verify: task_0003
- [x] Origin 白名单：跨源 /v1/* 403、本机来源与无 Origin（curl）放行 -> verify: task_0004
- [x] 前端：key 门删除、#bootstrap 自动换发、csrf 内存态、构建通过 -> verify: task_0005
- [x] 全量回归（CI 三步）+ 启动打印一键链接 smoke -> verify: task_0006

## 7. 风险与约束
- 风险点：Cookie 门破坏既有 api_key 测试 -> api_key 通道原样保留（二选一逻辑），适配成本仅 fixture
- 风险点：CSRF 豁免面过大 -> 仅 api_key 头请求豁免（外部程序无 cookie）；浏览器请求必有 cookie 故必校验
- 风险点：SSE 长连接的 cookie 时效 -> 会话生命周期=进程生命周期，无过期问题
