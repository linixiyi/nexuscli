# nexusCLI 自我迭代波 - 验收清单

## 功能验收

- [x] usage 覆盖面：子代理回合以 source=subagent 落库，/usage stats 出现 By source 分组；工作流步骤由同一落库点覆盖（调度器不写库，零 token 不落）
  - 证据：tests/test_usage_coverage.py；主线复跑 pytest 全绿
- [x] BM25 检索排序：AND 命中按相关性排序，输出格式不变，零依赖
  - 证据：tests/test_search_rank.py；rag/code_index.py:34-37,56
- [x] /workflow 并行执行器：依赖批次并行、上界可配、失败中止、每步独立 mailbox_name（general-purpose.<step-id>）
  - 证据：tests/test_workflow_parallel.py 15 项；workflow_cmds.py:53-54,62-118；config.py WorkflowConfig
- [x] 外围真实冒烟 3/3：team（108465 tokens、两文档标题在答案、多 worker 迹象）、mcp serve tools/list 25 工具、Runtime API 200/401
  - 证据：.zcode/smoke2/*.log；key 零落盘 grep 证实
- [x] 主流 Agent 对标：8 产品矩阵（pi 为本机实测 source=refs）、差距 14 条
- [x] 自我迭代 3 条：provider 429/5xx 瞬时错误自动压缩重试（llm/openai_compatible.py:82,223）、Agent Skills 规范兼容、provider key 经命令/密钥管理器读取

## 质量门（2026-10-11 主线亲手复跑）

- [x] uv run ruff check . → All checks passed!
- [x] uv run ruff format --check . → 177 files already formatted
- [x] uv run python -m pytest → 652 passed（590→652，净增 62；含带等待的重试类测试故耗时 5:44）
- [x] Mimosa 深度扫描 → 0 findings（scanId scan-2026-10-11T13-22-05.415Z，seal sha256:d03ede…）；1 条离线依赖 advisory 为 context-only 待联网复核

## 挂账（下一轮候选）

- 会话分支树 + 离开分支摘要（M）；按需工具检索 tool search（M）；MCP 工具级过滤（M）；scoped-models（S）；/export（S）
- REPL 人工冒烟、参考项目亮点第 6 条（等用户）
