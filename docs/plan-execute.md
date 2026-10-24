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
