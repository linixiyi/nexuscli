# 对齐第 5 轮：报告导出与协议文档链（A13/A14） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：A13 工作台运行报告冻结与 markdown 导出（kitagent projection.py:315-380 模式，
  注明「模型结论不代替检查证据」）；A14 Runtime 端点文档与实现同步的机械防护
  （kitagent scripts/generate_wire_protocol.py + check 模式）
- **目标用户**：工作台用户（导出会话报告）、维护者（端点文档不腐烂）
- **核心价值**：对齐计划的最后两项 medium 收尾

## 2. 假设与待确认

### 2.1 已确认事实
- 契约来源：差距矩阵 medium 的 A13/A14 行（kitagent 源路径 projection.py:315-380、
  scripts/generate_wire_protocol.py:87-460、check_wire_protocol.py）
- A3 已落鉴权关键词断言（第 1 轮）；A14 的完整版=从路由表生成端点文档 + CI 校验同步

### 2.2 关键假设
- 导出报告 = 前端从 transcript/usage 读端点组装 markdown 下载（无需新后端端点亦可达成，
  端点可选增强）
- A14 完整版从 api.py 的路由分发表生成文档表有实现风险（if/elif 链非数据表）——
  允许降级为「端点清单常量表 + 生成器 + CI 校验」三件套（数据表即单一事实源）

### 2.3 待确认问题
- [x] 无（A14 允许降级实现已定）

### 2.4 可选解释与取舍
- 当前选择：A13 导出为前端纯前端组装（fetch 已有端点拼 markdown Blob 下载），后端零改动
- 当前选择：A14 降级为「ROUTE_MANIFEST 常量 + 生成脚本 + check 脚本 + CI 步骤」

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] A13 工作台运行报告导出：transcript + usage 组装 markdown（含「模型结论不代替检查证据」
  声明）前端下载动作
- [x] A14 端点文档生成链：ROUTE_MANIFEST 常量表（单一事实源）→ scripts/generate_endpoint_docs.py
  生成 docs/runtime-api.md 的端点表节 → scripts/check_endpoint_docs.py 校验同步 → CI 步骤

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- kitagent 的 pydantic 模型生成全套 wire protocol（NexusCLI 非 JSON-RPC 形态）
- projection 状态机（第 2 轮已判定不对齐，只取报告冻结思路）

## 4. 最小实现路径
- 最简单可行方案：A13 前端 Blob 下载；A14 api.py 抽 ROUTE_MANIFEST 元组 + 两个脚本 + ci.yml 一行
- 暂不引入：导出格式选项（仅 markdown）、端点文档全文生成（仅端点表节）
- 不做的抽象/配置化：报告模板写死（kitagent 同款思路）

## 5. 技术决策
- 技术栈：前端 Blob/URL.createObjectURL + ast 解析（照搬 test_docs_integrity 的 ast 手法）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：frontend/src/views/（导出按钮/动作）、frontend/src/api/client.ts（如需）、
  src/nexuscli/runtime/api.py（抽 ROUTE_MANIFEST 常量，路由行为零变化）、scripts/generate_endpoint_docs.py（新）、
  scripts/check_endpoint_docs.py（新）、docs/runtime-api.md（生成节标注 Do-not-edit）、.github/workflows/ci.yml（同步校验步骤）、
  frontend/dist（重建）、tests/test_runtime_hardening.py（ROUTE_MANIFEST 一致性用例，可选）
- 本轮不应触碰：agent/、tools/、llm/、plan/、其他 docs/
- Git integration branch：`spec/2026-10-25_port-report-and-protocol-docs`

### 5.1 验证策略
- 范围级别：package
- 变更对象：前端导出动作、api.py 常量抽取（零行为）、生成/校验脚本、CI
- 快速检查：`uv run python -m pytest -q tests/test_runtime_hardening.py tests/test_docs_integrity.py`
- 集成检查：`uv run python scripts/check_endpoint_docs.py`（同步校验）+ `cd frontend && npm run build`
- 全项目检查：适用外——与 CI 一致的三步回归已由 task_0003 真实执行并全绿（1224 passed），升级条件见「升级触发」行
- 升级触发：若 ROUTE_MANIFEST 抽取改变任何路由行为即升级并记录

## 6. 成功标准与验证方式
- [x] A13：会话回放页有「导出报告」动作，下载的 markdown 含对话时间线/usage/「模型结论不代替检查证据」声明 -> verify: task_0001
- [x] A14：ROUTE_MANIFEST 为唯一事实源，生成脚本产出与 docs/runtime-api.md 端点表一致，check 脚本不同步即非零退出，CI 增步骤 -> verify: task_0002
- [x] 全量回归（CI 三步）+ 前端构建不破 -> verify: task_0003
- [x] master A13/A14 勾选、.spec/autorun/ 摘要写入 -> verify: task_0004

## 7. 风险与约束
- 风险点：ROUTE_MANIFEST 抽取触碰路由行为 -> 抽取为纯常量 + 既有路由测试回归锚
- 风险点：生成文档与手写文档冲突 -> 生成节用 HTML 注释围栏（Do-not-edit），手写节不动
