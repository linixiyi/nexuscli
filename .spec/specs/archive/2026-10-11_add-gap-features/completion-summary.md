# 完成总结 - 2026-10-11_add-gap-features

对照同类 Agent CLI 项目亮点清单的四项缺口全部交付，590 测试全绿，真实 LLM 冒烟 3/3 通过。

## 交付内容

- **G1 Agent 评测回归**：`nexuscli eval list/run`，剧本式假 LLM 驱动真实 Agent，四类断言，last-report.json 回归对比。新模块 src/nexuscli/eval/，内置场景 examples/evals/，docs/eval.md。
- **G2 工具循环调用检测**：src/nexuscli/agent/loop_guard.py 纯函数指纹计数 + agent.py 接入；agent.loop_detection_threshold（默认 3，0 关闭）；拦截提示→终止 warning 两级。
- **G3 run 级授权**：审批第 4 档 r（approve_run），ToolContext.run_scope_approved per-run 集合，审计 approver=run-scope；单次/run/会话/全局四级凑齐。
- **G4+G5 会话增强**：/history 跨会话搜索（--all 全项目）；每回合 usage 行入转录、恢复基线续算、/usage 与 --json 双口径、四条切换路径重置。

改动面：17 文件修改 + 10 新增（+773/-32），测试 490→590。

## 处置与遗留

- 独立复审 8 条 findings 全部处置；其中「run 级不传导子代理」按语义定界写入 docs/subagents.md。
- 遗留：亮点第 6 条待补图评估；REPL 人工冒烟待人工；usage.db 覆盖面扩展进入下一任务包（2026-10-11 起的自我迭代波）。
