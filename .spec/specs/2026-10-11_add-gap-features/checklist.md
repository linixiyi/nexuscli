# nexusCLI 参考项目差距四项补齐任务包 - 验收清单

## 功能验收

- [x] G1 评测回归：`nexuscli eval list` / `eval run` 可用；examples/evals 3 个内置场景；last-report.json 前后对比标 regression/fixed
  - 证据：tests/test_eval.py 通过；docs/eval.md 完整口径
- [x] G2 循环检测：agent.loop_detection_threshold（默认 3，0 关闭）；连续同指纹先拦截提示、再终止带 warning
  - 证据：tests/test_loop_detection.py 全部场景通过；config 回退风格对齐 max_turns
- [x] G3 run 级授权：审批选项 y/n/a/s/r；run-scope 审计 approver；deny/ask/hook 优先级不破坏；新 run 清空
  - 证据：tests/test_run_scope_approval.py 通过；审计测试已 patch HOME
- [x] G4 历史搜索：/history <query> [--all] 跨会话检索，提示 /resume <id>
  - 证据：tests/test_history_search.py 通过
- [x] G5 usage 续算：每回合 usage 行入转录；恢复基线；/usage 与 --json 双口径；四条切换路径 process_usage 重置
  - 证据：tests/test_session_usage.py + test_session_store.py 通过；CLI --json 实测输出含 session_usage.historical_total/current_process 与 sources 标注（真实 LLM 冒烟顺带验证）

## 质量门（与 CI 同口径，2026-10-11 本机实测）

- [x] uv run ruff check . → All checks passed!
- [x] uv run ruff format --check . → 171 files already formatted
- [x] uv run python -m pytest → 590 passed in 27.28s
- [x] 独立代码复审 8 条 findings 全部处置（2 medium 实修：process_usage 重置于 repl.py:375,392,583 与 session_cmds.py:66；--json 双口径于 cli.py:626,635）

## 真实 LLM 冒烟（同日追加）

- [x] cli-react（含 read_file 工具调用，total 9978 tokens）/ cli-plan（2 任务 DAG，total 22299 tokens）/ sdk-react（total 4842 tokens）3/3 通过，模型 cbcn/glm-5.3-flash，复核员逐条确认证据、无密钥泄露

## 如实边界

- 亮点清单第 6 条截图截断未评估；REPL 交互式人工冒烟仍待人工执行；usage.db 仍只覆盖主 REPL 回合（子代理/workflow 回合不落库，属下一任务包范围）
