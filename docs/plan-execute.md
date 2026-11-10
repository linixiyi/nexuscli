# Plan-and-Execute 检查点与续跑

> 本文是 [README](../README.md) 深入文档索引「Plan-and-Execute 检查点与续跑」条目的完整版。

`/plan`（Plan-and-Execute 模式）与 team 的嵌套 plan worker 默认开启**图级 checkpoint**：Planner 产出计划后、每个任务执行完成后、计划到达终态（完成/失败/停滞）后各落盘一次，文件在 `~/.nexuscli/plan_runs/<plan_id>.json`（原子写入；checkpoint 写失败不杀运行）。plan id 带毫秒时间戳 + 随机后缀，保证并行 worker 同时产出计划也不会互相覆盖。

中断后续跑：

```text
/plan --resume <plan_id>     # 跳过规划，直接从 checkpoint 续跑剩余任务
```

- 续跑只执行 PENDING 任务：已完成任务的结果从 checkpoint 原样保留并注入后续任务的上下文，**不会重复烧 token**；崩溃窗口内被持久化为 RUNNING 的任务在恢复时自动归一化为 PENDING 重新执行。
- checkpoint 存储对 plan id 做文件名安全校验（拒绝路径分隔符与 `..` 链），未知/损坏/非法 id 一律按「未找到」降级，不抛异常。

**协作式中断**：`PlanExecuteAgent.request_interrupt()` 置位后，执行循环在**批次之间**停机并落盘，产出 `plan_interrupted` 事件与续跑提示。如实边界：批内已派发的任务会跑完（检查点在批次间）；该 API 目前是 agent 编程接口，REPL 的 `Ctrl+C` 仍是中止回合而非协作式中断，team worker 侧的 interrupt 接线挂账。

**如实边界**：checkpoint JSON 以明文保存任务结果全文，普通 `/plan` 运行也会留下文件，目前**没有 TTL/容量清理**（体积敏感时手动清理 `~/.nexuscli/plan_runs`）；resume 语义是重入中断的运行而非新会话轮——不写入会话历史、不做 pre/post-turn 快照；`/workflow`（JSON 步骤图）与 plan 模式（Shift+Tab 只读态）是另外两套机制，均无断点续跑语义。

## 与 `--mode plan` / Shift+Tab 只读态的区别

名字里带「plan」的机制有三套，别混用：

| 机制 | 入口 | 语义 |
|---|---|---|
| 只读规划 | `nexuscli --mode plan -p "..."` | 单次模式跑 Plan-and-Execute：只读闸门全程开启，不会执行任何写入；双重约束收紧——planner 收到只读规划指令，计划任务只允许 read_file/glob/grep/search_code/vfs_ls/vfs_read 六个只读工具，禁止安排 bash/execute_command 任务（即使命令看似只读），也禁止安排 pytest、npm、uv run、安装依赖、构建、写盘类任务；worker 运行时只看到这六个只读工具（registry 白名单过滤，bash/write_file 等写入与命令工具不进 worker 的工具列表）；即使有漏网的写入/命令调用，执行层只读闸仍会拒绝并按 blocked 收场；`--yes` 在该模式无效（启动时打印 warning 并忽略）、不能绕过只读闸，计划被只读闸门拦下按失败收场（exit 1），也没有 `--resume` 续跑入口 |
| 交互式 Plan-and-Execute | REPL `/plan <task>`（`/plan --resume <plan_id>` 续跑） | 与 `--mode plan` 同一只读闸门口径，但在交互会话里流式执行，支持本文所述的图级 checkpoint、协作式中断与续跑 |
| REPL 只读态 | 交互模式 `Shift+Tab` 切到 `plan (read-only)` | 会话权限模式：把整个 REPL 会话（含普通对话）置于只读闸门下，不产出计划 DAG；审阅方案后切回 Default 再落地执行 |

`--mode plan` 与 `/plan` 都不执行写入；`plan (read-only)` 的权限语义见 [permissions.md](permissions.md#会话权限模式default--auto--plan)。

## plan 模式下的 worker 只读约束

plan 模式（`--mode plan` 与 REPL `/plan`）对任务 worker 是**双重约束**：

1. **planner 指令白名单**：规划阶段注入只读指令，计划任务只允许安排 read_file/glob/grep/search_code/vfs_ls/vfs_read 六个只读工具对应的读、检索、分析类任务，禁止安排 bash/execute_command 与写盘类任务；
2. **worker 运行时 registry 过滤**：每个任务 worker 的工具 registry 被过滤成 read_file/glob/grep/search_code/vfs_ls/vfs_read 六个只读工具的子集——bash/write_file/edit_file 等写入与命令工具根本不进 worker 的工具列表与系统提示词，worker 想调也调不到。

两层之后仍有**执行层只读闸**兜底：`tools/executor.py` 的 plan 只读闸继续拒绝任何漏网的写入/命令调用（含被白名单隐藏后仍被尝试调用的工具），对应任务按 blocked 收场、绝不标 COMPLETED。`--yes` 只影响审批、不能绕过这只读闸。写入动作请切回默认模式（`Shift+Tab`，或 `-p` 时去掉 `--mode plan`）后再执行。
