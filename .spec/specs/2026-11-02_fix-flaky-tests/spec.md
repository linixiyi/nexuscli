# 全量 pytest 间歇失败：检测、根因修复与稳定性确认 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：定位并修复全量 pytest 的间歇性失败（flaky tests）——本会话在包 5
  （1 例）与包 D（2 例，单独重跑通过、最终全量重跑绿）的验收中反复出现，失败测试名
  均未被留存。本包：①系统性检测（多轮全量 + 嫌疑文件压力循环）抓到失败样本与名字；
  ②逐例根因修复（测试缺陷修测试，产品缺陷修产品并说明）；③修复后连续 3 轮全量绿
  确认。
- **目标用户**：本仓开发者——flaky 直接侵蚀 CI 门禁与所有「全绿」结论的可信度。
- **核心价值**：让每一个 PASS 都立得住；失败样本与根因入档，终结「重跑通过」的
  处理惯性。

## 2. 假设与待确认

### 2.1 已确认事实
- 已观测两次间歇失败（包 5 manual-evidence、包 D manual-evidence），均「重跑通过」，
  未捕获测试名与 traceback——检测是第一必要步骤
- 全量 pytest 约 415 秒（1566-1610+ 用例规模）；flaky 率粗估 1-2 例 / 6+ 轮观测，
  单轮全量命中率低 → 检测设计为 4 轮全量 + 嫌疑文件压力循环
- 嫌疑面（按新近性与机制）：test_telemetry_otlp（ThreadingHTTPServer 端口绑定）、
  test_skill_propose_pty（pty select 循环 + 45s 时限）、test_eval_*（reporter 写
  .nexuscli/evals 相对路径）、test_mcp_oauth / test_mcp（子进程/端口）、
  test_eval_baseline_ablation / test_approval_semantics / test_skill_*（最新落地面）
- 包 D 工作流报告注记：全量曾现 2 failed → 单测重跑通过 → 最终全量重跑 exit 0
- 工作流 world.run 可设长超时（spec_verify 的 600s 上限不约束工作流内检测）

### 2.2 关键假设
- flaky 根因集中在四类机制：共享临时目录/路径、端口绑定竞态、时序 sleep 断言、
  全局单例状态跨测试泄漏（conftest reset 缺口）
- 4 轮全量 + 每个嫌疑文件 5 轮压力足以抓到至少 1 例；若抓不到，如实按 accepted_risk
  收口（检测证据本身入档），不伪造发现
- 修复优先改测试（隔离不彻底、断言时序依赖）；若根因在产品（真竞态/真泄漏），修产品
  并在包内说明——两类都要有「为什么以前会偶发」的解释

### 2.3 待确认问题
- [x] 若 4+4 轮检测零命中怎么办？→ 结论：accepted_risk 收口——检测协议与证据入档，
  下次任何全量失败立即回填本包档案重启；不虚报「已修复」

### 2.4 可选解释与取舍
- 当前选择：检测与修复同包串行（不做独立检测包）-> 理由：发现到修复的上下文连续性
  价值高于包粒度洁癖
- 当前选择：不引入 pytest-randomly/xdist 等检测增强依赖 -> 理由：零新依赖原则；
  多轮重跑已够
- 当前选择：修复验证标准 = 受影响文件单测 + 连续 3 轮全量绿 -> 理由：3 轮 ≈ 21 分钟，
  与既有 flaky 率匹配的信心下限

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 检测扫描：4 轮全量（完整输出存档，不截断）+ 嫌疑文件 5 轮压力循环，产出
  evidence/flaky-findings.json（每例：测试名、轮次、traceback 全文、嫌疑分类）
- [x] 逐例根因修复：每例修测试或修产品，附根因解释；受影响文件测试过
- [x] 稳定性确认：修复后连续 3 轮全量绿（逐轮结果入 evidence）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 检测类依赖引入（pytest-randomly 等）、CI 重试机制（掩盖而非修复）、性能优化

## 4. 最小实现路径
- 最简单可行方案：检测脚本（bash 循环 + 完整日志落包内 evidence/）+ 逐例小修 +
  3 轮确认；零新依赖
- 暂不引入：flaky 检测的常驻机制、测试分片
- 不做的抽象/配置化：不做「测试稳定性」框架

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：tests/**（测试修复）、src/**（仅当根因为产品缺陷，逐例说明）、
  包内 evidence/**、AGENTS.md 增量提议（经 done 流程）
- 本轮不应触碰：场景与 manifest 数据、docs 正文、README、frontend
- Git integration branch：`spec/2026-11-02_fix-flaky-tests`

### 5.1 验证策略
- 范围级别：package
- 变更对象：tests 修复点、（如有）src 缺陷点
- 快速检查：`uv run --extra dev python -m pytest -q <受影响文件>`
- 集成检查：`uv run --extra dev python -m pytest -q` × 连续 3 轮全绿
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；closeout 由发布
  门禁统一执行
- 升级触发：若产品根因修复需要动共享基础设施（如 conftest 全局重排），停下升级并记录

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：检测/修复/确认由主会话委托动态工作流子代理执行（用户显式要求）；主会话
  保留路由、验收、done/push 门禁

## 6. 成功标准与验证方式
- [x] 检测协议执行完毕：4 轮全量 + 嫌疑压力循环的完整证据落档（无论是否命中）->
  verify: task_0001
- [x] 每个命中 flaky 有根因修复与「为何偶发」解释，受影响文件测试过（零命中则
  accepted_risk 处置入档）-> verify: task_0002
- [x] 修复后连续 3 轮全量绿，逐轮结果入 evidence -> verify: task_0003

## 7. 风险与约束
- 风险点：flaky 率太低检测抓不到 -> 缓解：4 轮全量 + 嫌疑文件 5 轮压力 + accepted_risk
  的诚实出口
- 风险点：修复引入新失败 -> 缓解：逐例小修 + 每修必跑受影响文件 + 3 轮全量确认
- 风险点：检测轮次与修复轮次的日志膨胀 -> 缓解：完整日志只存包内 evidence/，不进 src
