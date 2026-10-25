# provider 细节与 mypy 纪律（F12/F13） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：F12——Anthropic provider 对齐 kitagent 已验证的细节（max_tokens 截断合成
  tool_result、thinking signature 回传核实、传输层流断线退避重试、prompt caching 打位）；
  F13——mypy 渐进接入 CI（存量排除清单只减不增）
- **目标用户**：使用 Anthropic 原生 provider 的用户；仓库维护者（类型纪律）
- **核心价值**：provider 协议级正确性（块配对不变量）与可维护性（类型检查进门禁）

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约：05-execution-durability.md F12/F13 节（F12 现状盘点表已定稿：四项均缺、cache_read 映射已有；
  F13 实测摸底基线 100 errors in 30 files/122，逐文件分布已录）
- 05 的 F11 已交付 results.py（error_code 机制可复用于 provider 错误面）
- 本轮经用户授权用子代理 /workflow 并行实现（主会话验证收口）

### 2.2 关键假设
- F12 的修改半径在 src/nexuscli/llm/anthropic.py + agent.py 的截断处理（盘点表定位）
- F13 排除清单以摸底实测为准；新改动模块必须通过

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：F12 只动 llm/ 与 agent.py 的盘点表定位点 -> 不重写客户端
- 当前选择：F13 不承诺全仓清零 -> 排除清单只减不增（master 边界）

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] F12 Anthropic provider 细节对齐（max_tokens 合成/退避重试/caching 打位/thinking 核实）
- [x] F13 mypy 渐进接入 CI（配置 + 排除清单 + CI job）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 全仓 mypy 清零承诺；OpenAI-compatible 路径的同款重试（挂账）；llm/ 重写

## 4. 最小实现路径
- 最简单可行方案：F12 按 05 盘点表逐项补齐并补核实测试；F13 mypy.ini/pyproject 配置 + 排除清单 + CI job
- 暂不引入：mypy 插件、strict 全仓、provider 重试的通用抽象
- 不做的抽象/配置化：退避参数写常量（1/2/4s 对标 kitagent）

## 5. 技术决策
- 技术栈：pytest + mypy + GitHub Actions
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：src/nexuscli/llm/anthropic.py、src/nexuscli/agent/agent.py（截断处理定位点）、tests/test_anthropic_client.py、pyproject.toml 或 mypy 配置节、.github/workflows/ci.yml、mypy 排除清单
- 本轮不应触碰：tools/、runtime/、plan/、REPL 渲染、docs/
- Git integration branch：`spec/2026-10-25_update-provider-details-mypy`

### 5.1 验证策略
- 范围级别：package
- 变更对象：llm/anthropic.py、agent.py 截断点、mypy 配置与 CI
- 快速检查：`uv run python -m pytest -q tests/test_anthropic_client.py`
- 集成检查：`uv run --with mypy mypy src/nexuscli`（排除清单生效后通过）
- 全项目检查：适用外——与 CI 一致的三步回归已由 task_0003 真实执行并全绿（1131 passed），升级条件见「升级触发」行
- 升级触发：若 mypy 暴露跨模块真实缺陷（非标注缺口），就地修复并记录

## 6. 成功标准与验证方式
- [x] F12：max_tokens 截断在工具调用中间时合成 is_error tool_result（不执行截断调用） -> verify: task_0001
- [x] F12：传输层流断线（RemoteProtocolError/ReadError/ConnectError）1/2/4s 退避重试、语义错误不重试 -> verify: task_0001
- [x] F12：prompt caching 打位（system + 最后一个工具）与 thinking signature 回传核实 -> verify: task_0001
- [x] F13：mypy 配置 + 排除清单 + CI job，`uv run --with mypy mypy src/nexuscli` 通过 -> verify: task_0002
- [x] 全量回归（CI 三步）不破 -> verify: task_0003
- [x] master F12/F13 勾选、.spec/autorun/ 摘要写入 -> verify: task_0004

## 7. 风险与约束
- 风险点：重试期间 token 事件重复 -> 只发布第一次的 token 事件（kitagent 手感）
- 风险点：mypy 排除清单漂移 -> 只减不增写进配置注释与 CI
- 风险点：子代理实现的代码质量 -> 主会话全量回归 + 逐项验收把关
