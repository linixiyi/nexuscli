# 夜间①功能全量验证基线 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：夜间接力第①棒——对 NexusCLI 做一次全量、有真实证据的功能验证，产出按严重度分级的缺陷与改进清单（findings.md），供第②③棒修复/升级直接取用
- **目标用户**：后续夜间接力棒次（修复/升级/UI/文档）与晨间使用者
- **核心价值**：得到当前真实健康度基线与一份可执行、带证据的 findings.md

## 2. 假设与待确认

### 2.1 已确认事实
- NEXUS.md 已允许自动提交与推送（spec done/push 直推 main）
- 测试统一入口 `uv run --extra dev python -m pytest`；前端在 `frontend/`（React 19 + Vite + playwright e2e）
- 本棒只验证与记录，不改任何业务代码

### 2.2 关键假设
- 本机已装 pytest/e2e 所需依赖；若 e2e 缺浏览器环境，如实记录为外部阻塞而非失败
- 后台并行执行 pytest/ruff/build 与模块核查互不干扰

### 2.3 待确认问题
- [x] e2e 是否需要先起 Runtime API 服务（按 frontend/e2e 实际配置与 playwright.config 判断，执行时回填结论）

### 2.4 可选解释与取舍
- 当前选择：核心模块核查用 workflow fan-out 只读子代理 -> 用户明确要求用 workflow 编排并行段；被阻塞或报错则降级为普通 Agent 子代理或亲自执行
- 当前选择：全量命令可能非零退出，verify 命令统一追加 `printf` 回显真实退出码 -> evidence: runs 门禁要求 exit==0，而本包的交付物恰是"验证结果"本身，真实结果记录在 runs.jsonl 的 stdout 里

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 全量验证：pytest / ruff check / ruff format / uv build / CLI 冒烟 / 前端 build+e2e 全部真实执行并记录
- [x] findings.md：P0/P1/P2 缺陷与改进机会清单，每条含位置(文件:行)、现象、证据(命令输出)、建议修法、预估工作量，末尾遗留清单

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 修复任何缺陷（第②棒）、功能升级（第③棒）、UI 改动（第④棒）、文档同步（第⑤棒）

## 4. 最小实现路径
- 最简单可行方案：全量命令验证 + 只读子代理分片核查核心模块，主会话汇总为单个 findings.md 放包内
- 暂不引入：修复补丁、性能剖析工具、新测试框架、CI 变更
- 不做的抽象/配置化：不做验证脚本化封装，findings.md 手写汇总；why：一次性记录，抽象无第二实现者

## 5. 技术决策
- 技术栈：uv + pytest + ruff + vite/playwright
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：仅 `.spec/specs/2026-10-27_night-verify-baseline/` 包内文件
- 本轮不应触碰：`src/`、`frontend/src/`、`tests/`、`README.md` 等一切业务文件
- Git integration branch：`spec/2026-10-27_night-verify-baseline`

### 5.1 验证策略
- 范围级别：project
- 变更对象：仅包内文档；验证对象为全仓（本包交付物即全项目验证结果）
- 快速检查：`uv run --extra dev ruff check .`
- 集成检查：task_0001 全量 pytest
- 全项目检查：task_0002 构建 + task_0004 前端 e2e
- 升级触发：验证暴露跨模块回归时写入 findings 供第②棒处理，本包不扩权修改

## 6. 成功标准与验证方式
- [x] 后端全量回归真实执行且结果已记录 -> verify: task_0001
- [x] 静态检查与 uv build 真实执行且结果已记录 -> verify: task_0002
- [x] CLI 冒烟（--version 与 doctor）真实执行且结果已记录 -> verify: task_0003
- [x] 前端构建与 e2e 真实执行且结果已记录 -> verify: task_0004
- [x] findings.md 产出且含 P0/P1/P2 分级清单、证据与遗留清单 -> verify: task_0005

## 7. 风险与约束
- 风险点：e2e/浏览器环境缺失或 Runtime API 未起 -> 缓解：如实记录为外部阻塞，不谎报通过
- 风险点：模块核查子代理结论失真 -> 缓解：每条 finding 必须带可复验的文件:行与命令证据，主会话抽查后才写入 findings.md
- 风险点：长跑跨 08:55 硬停 -> 缓解：每棒前对表，本包按优先级先保全量命令结果，模块核查可裁片数
