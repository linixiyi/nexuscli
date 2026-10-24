# 完成总结 - 2026-10-11_self-iterate-upgrades

四项升级全部落地、主流 Agent 对标产出 14 条差距、自我迭代实现 3 条；652 测试全绿，Mimosa 深度扫描 0 findings，外围真实冒烟 3/3。

## 交付

- **usage 覆盖面**：turn_usage 增 source 列（迁移兼容），子代理回合 source=subagent 落库，/usage stats 按 source 分组；工作流步骤经统一落库点覆盖。
- **BM25 检索排序**：纯 Python tf/IDF 打分（k1=1.5/b=0.75），AND 语义与输出格式不变，Top-N 可配。
- **/workflow 并行执行器**：依赖批次并行派发（max_parallel_steps 默认 3，1=串行等价），失败不派新批次，每步独立 mailbox_name 解决并发收件箱串读。
- **自我迭代 3 条**：provider 瞬时错误（429/5xx）自动压缩重试；Agent Skills 规范兼容；provider key 经命令/密钥管理器读取。
- **对标结论**：8 产品矩阵（pi 本机实测）；14 条差距中 5 条评估后挂账（会话分支树、tool search、MCP 工具过滤、scoped-models、/export）。

## 流程

复审 8 条（4 medium）经集成结构性处置（调度器不落库 + 每步独立邮箱名）；[最终] 门第 1 轮 pytest 失败修复后第 2 轮全绿；主线验收复跑 652 passed。
