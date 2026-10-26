# 夜间③功能升级第二轮 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：落实夜间① findings.md 中可独立交付的四项 P2 升级——审计值级掩码（Bearer/JWT）、Runtime 任务最大重试上限、usage.db 保留窗口清理、/turn-resume 入命令清单——全部带回归测试
- **目标用户**：长期使用 NexusCLI 的真实用户（审计消费者、Runtime 部署者、REPL 用户）
- **核心价值**：可观测性与运维健壮性——审计不再落 JWT 明文、毒任务不占死 worker、usage.db 不无界膨胀、隐藏命令可发现

## 2. 假设与待确认

### 2.1 已确认事实
- audit_log.py:_mask_tokens 现只覆盖 sk-/ghp/AKIA 三种模式（夜间① P2-8 实测 JWT 明文落盘）
- runtime/tasks.py:requeue_expired 无 attempts 门（P2-19）；claim_next 每次 attempts+1
- usage_store.py docstring 自述 retention 未移植（P2-16）；DDL 列名 ts，record_turn 有 ts 测试缝
- /turn-resume 已实现（repl.py:654-741）且有 docs 专节，但不在 SLASH_COMMANDS（P2-6）

### 2.2 关键假设
- 保留窗口默认 90 天足够；清理 best-effort（suppress），绝不打断 stats
- 重试上限默认 5 次对 team/后台任务是合理平衡；上限只作用于租约恢复路径
- SLASH_COMMANDS 既有测试只断言成员关系，新增项安全（已核对）

### 2.3 待确认问题
- [x] P2-20（Runtime API MCP 会话跨 turn 复用）是否本轮做：时间富余则做，否则记遗留 -> 结论：记遗留——评估发现 Runtime API 每请求独立线程+asyncio.run 独立事件循环，共享 McpClientManager 牵涉跨 loop 的 anyio 会话语义（client.py abandon/drop），须带真实有状态 MCP server 的集成验证，不宜无人值守夜间改

### 2.4 可选解释与取舍
- 当前选择：重试上限只清 requeue_expired（cull 转 failed），claim_next 不加 attempts 门 -> requeue 是唯一把 attempts 抬高的恢复路径，单点收口足够；claim 门会让上限语义分散两处
- 当前选择：prune 挂在 stats() 内 best-effort 而非独立 worker -> 模块本身 best-effort 语义，不引入后台线程
- 当前选择：审计掩码加 Bearer/JWT 两条值级正则而非通用熵检测 -> 确定性优先，熵检测误报率高

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] P2-8：audit_log 值级掩码——bash command 串里 `Bearer <token>` 与 JWT（eyJ 三段式）落盘前掩码
- [x] P2-19：requeue_expired 加 max_attempts（默认 5），超限任务转 failed 带原因
- [x] P2-16：UsageStore.prune(keep_days=90) + stats() 内 best-effort 调用
- [x] P2-6：/turn-resume 进 SLASH_COMMANDS 与 docs/repl-commands.md 总表

### 3.2 扩展功能
- P2-20 Runtime API McpClientManager 提升到服务器生命周期（时间允许时）

### 3.3 不在范围内
- 其余 P2（journal 接线、LoopGuard 下沉等，留晨间）、workbench UI（第④棒）、文档全面同步（第⑤棒）

## 4. 最小实现路径
- 最简单可行方案：四项各自独立小改 + 各一个回归测试；不做共享抽象
- 暂不引入：保留窗口配置项（写死 90 天默认，参数已暴露）、重试上限配置项（函数参数可调）、审计掩码开关
- 不做的抽象/配置化：P2-20 若做也只在 api.py 内提升 manager 实例，不改 client 接口

## 5. 技术决策
- 技术栈：既有 sqlite/policy 机制内的小改
- 证据协议：evidence: runs
- 本轮允许改动：src/nexuscli/policy/audit_log.py、src/nexuscli/runtime/tasks.py、src/nexuscli/llm/usage_store.py、src/nexuscli/entrypoints/repl.py（仅 SLASH_COMMANDS 列表）、docs/repl-commands.md、tests/{test_audit_redaction,test_runtime,test_usage_history,test_slash_commands,test_workflow}.py
- 本轮不应触碰：frontend/、agent/、mcp/、context/
- Git integration branch：`spec/2026-10-27_night-enhance-r2`

### 5.1 验证策略
- 范围级别：integration
- 变更对象：审计脱敏输出、runtime 任务恢复路径、usage 聚合入口、REPL 命令清单
- 快速检查：`uv run --extra dev python -m pytest tests/test_audit_redaction.py tests/test_runtime.py tests/test_usage_history.py tests/test_slash_commands.py tests/test_workflow.py -q`
- 集成检查：task_0005 全量 pytest（SLASH_COMMANDS 被多文件断言，必须全绿）
- 全项目检查：仅在收尾或升级触发时执行——task_0005 全量 pytest + ruff + uv build
- 升级触发：全量回归暴露跨模块漂移，或 docs 命令表与实现失配时，回写本包并本轮修复

## 6. 成功标准与验证方式
- [x] bash command 里的 Bearer/JWT 在审计行中为掩码 -> verify: task_0001
- [x] 硬死亡任务超上限转 failed 不再无限重派 -> verify: task_0002
- [x] usage.db 窗口外旧行被清理且统计正常 -> verify: task_0003
- [x] /turn-resume 出现在 SLASH_COMMANDS 与 docs 总表 -> verify: task_0004
- [x] 全量 pytest + ruff + uv build 通过 -> verify: task_0005

## 7. 风险与约束
- 风险点：掩码正则误伤普通命令文本 -> 缓解：JWT 模式要求三段式 eyJ 前缀，Bearer 要求词边界+长度；测试含反例
- 风险点：重试上限误杀慢任务 -> 缓解：心跳续租的正常任务不受影响（只在 lease 过期路径计数）；上限 5 次且带 error 原因可查
- 风险点：stats() 内删行影响并发读 -> 缓解：busy_timeout 30s 已有，删除行数小且 best-effort
