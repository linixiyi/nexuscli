# 对齐第 1 轮：零耦合小件照搬（A1-A4） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：按差距矩阵照搬 kitagent 四个零耦合小件——HTTP 安全壳、写端点幂等门、
  文档鉴权滞后修复+机械断言、事件日志 repair_tail——三 lane 并行（子代理 /workflow）
- **目标用户**：工作台用户（安全头）、外部自动化调用方（幂等）、维护者（文档不烂）
- **核心价值**：MIT 照搬最高价值的小件，出处注释保留，主代理上下文最短

## 2. 假设与待确认

### 2.1 已确认事实
- 契约来源：差距矩阵（对齐调研 workflow）high 的 A1/A2 行与 medium 的 A3/A4 行，含 kitagent 源路径
- kitagent 源码 /tmp/kitagent-src 实读可用；MIT 许可
- docs/workbench.md 与 runtime-api.md 鉴权描述滞后于 F14（矩阵 grep 证实无 bootstrap/csrf 字样）

### 2.2 关键假设
- A1 与 A2 同改 api.py → 同 lane 串行实现避免冲突；三 lane 文件面不相交
- repair_tail 移植到 SessionWriter/reads 为增量增强，不改既有原子替换语义

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：CSP 的 style-src 保留 unsafe-inline（前端 index.css 为构建产物外链，但 Vite 可能内联小样式——按 kitagent 原文照搬，实测构建后如破样式再收窄并记录）

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] A1 HTTP 安全壳照搬（api.py 统一响应头 + 413/415 前置门）
- [x] A2 写端点幂等门（request_id + payload 摘要登记）
- [x] A3 文档鉴权滞后修复 + 完整性断言
- [x] A4 事件日志 repair_tail 移植

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 矩阵其余 39 项（后续轮次）；CSP 收窄调优；repair_tail 的 fsync 级别调整

## 4. 最小实现路径
- 最简单可行方案：三 lane 照搬——lane1 api.py（A1+A2 串行）、lane2 docs+tests（A3）、lane3 session/reads（A4）
- 暂不引入：CSP report-only 模式、幂等表持久化（内存登记，kitagent 同款 1000 容量）
- 不做的抽象/配置化：响应头/容量上限写模块常量（kitagent 同款）

## 5. 技术决策
- 技术栈：stdlib（http.server headers / sqlite3 / json）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/runtime/api.py、src/nexuscli/session/（或 runtime/reads.py）、docs/workbench.md、docs/runtime-api.md、tests/test_docs_integrity.py、tests/（新增安全壳/幂等/repair_tail 测试）
- 本轮不应触碰：frontend/、agent/、tools/、llm/、.github/、.spec/plans/
- Git integration branch：`spec/2026-10-25_port-alignment-quick-wins`

### 5.1 验证策略
- 范围级别：package
- 变更对象：runtime/api.py 响应面与写端点、session 写读路径、docs 鉴权节
- 快速检查：`uv run python -m pytest -q tests/test_runtime_websession.py tests/test_runtime.py`
- 集成检查：`uv run python -m pytest -q tests/test_docs_integrity.py tests/test_runtime_sse.py tests/test_runtime_reads.py`
- 全项目检查：适用外——与 CI 一致的三步回归已由 task_0004 真实执行并全绿（1160 passed），升级条件见「升级触发」行
- 升级触发：若安全壳/幂等门破坏既有鉴权或 SSE 测试即升级并记录

## 6. 成功标准与验证方式
- [x] 统一响应头（CSP/no-store/nosniff/no-referrer）出现在所有响应；64KiB 体长 413；非 GET 非 JSON 415 -> verify: task_0001
- [x] 写端点幂等：同 request_id 同 payload 重放返回首次结果；同 ID 异 payload 409；容量满 429 -> verify: task_0001
- [x] docs/workbench.md 与 runtime-api.md 鉴权节反映 bootstrap/csrf/cookie；完整性断言通过 -> verify: task_0002
- [x] SessionWriter/reads 具备崩溃尾修复与严格校验（构造半行 JSON 测试） -> verify: task_0003
- [x] 全量回归（CI 三步）不破 -> verify: task_0004
- [x] master A1-A4 勾选、.spec/autorun/ 摘要写入 -> verify: task_0005

## 7. 风险与约束
- 风险点：CSP 破坏前端样式/内联脚本 -> kitagent 原文含 unsafe-inline style；构建后人工 smoke 首页
- 风险点：幂等门误伤既有测试的重复 POST -> 登记键为显式 request_id（缺失请求不进门），兼容零适配
- 风险点：repair_tail 改变读取容错语义 -> 仅在损坏时截断并如实返回，正常路径零变化；测试锁行为
