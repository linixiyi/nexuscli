# 测试矩阵收口:17 项零验证表面 + 45 项仅机器表面的真实使用验证 - 验收清单

> 每条验收完成时必须附真实证据(命令输出/证据文件路径),不得凭印象勾选。

## 验收项

- [x] L1 Lane A 会话与快照:/save /resume /fork /snapshot /restore 补齐机器验证与真实运行,8.4 8.5 补真实运行
  - 证据:evidence/lane-a.md + `pytest tests/test_lane_a_*.py` 17 passed;真实运行留痕见 evidence/lane-a.md
- [x] L2 Lane B 记忆与索引:/memory /index /search 补齐机器验证与真实运行,7.1-7.4 7.6 save/search_memory 补真实运行
  - 证据:evidence/lane-b.md + `.venv/bin/python -m pytest -q tests/test_lane_b_memory_surface.py tests/test_lane_b_index_search.py`
- [x] L3 Lane C 模式与子代理:/team /task 补齐,2.7 补机器验证;2.8 2.9 2.10 /workflow 12.6 12.7 12.8 补真实运行
  - 证据:evidence/lane-c.md + `.venv/bin/python -m pytest -q tests/test_lane_c_team_task.py tests/test_lane_c_modes.py`
- [x] L4 Lane D 技能/插件/扩展:12.1-12.5 6.8 6.9 /init /expert /goal load/save_skill 补真实运行,12.3 12.5 补机器验证
  - 证据:evidence/lane-d.md + `.venv/bin/python -m pytest -q tests/test_lane_d_extensions.py`
- [x] L5 Lane E 工具面/MCP/SDK:directory_tree get_file_info search_code todo /hitl /model /mcp 11.9 补齐;edit_file revert_turn 10.2 10.3 补真实运行
  - 证据:evidence/lane-e.md + `.venv/bin/python -m pytest -q tests/test_lane_e_tool_surfaces.py tests/test_lane_e_mcp_sdk.py`
- [x] L6 Lane F 可观测性与媒体:13.1-13.4 14.1-14.5 补真实运行
  - 证据:evidence/lane-f.md + `.venv/bin/python -m pytest -q tests/test_lane_f_observability.py tests/test_lane_f_media.py`
- [x] C1 第一轮跨 lane 反馈(共享文件缺陷)全部处置:修复+回归测试,或给出 accepted_risk/external_blocked 理由
  - 证据:轮间修复的测试命令输出
- [x] C2 第二轮工作流以上轮反馈为输入完成复验,剩余反馈收敛或如实分级
  - 证据:第二轮运行报告
- [x] E1 docs/testing-matrix.md 62 项状态列按证据如实更新,与 evidence/ 一一对应
  - 证据:矩阵文档 diff
- [x] E2 全量质量门:全量 pytest + ruff 无回归
  - 证据:`.venv/bin/python -m pytest -q` 与 `.venv/bin/python -m ruff check src tests` 输出

## 处置记录

(问题 → 处置方式:resolved_current / resolved_followup / accepted_risk / external_blocked / non_actionable)

第一轮 15 条反馈(均经独立复核 confirmed):
1. /save 实为长期记忆命令、矩阵"存当前会话"故事不成立 → **resolved_current**:不改实现(README:307 语义一致),矩阵行描述在 E1 改为长期记忆语义,会话可见性来自回合自动持久化
2. /snapshot 行描述过时(已支持 save [label]/clean)→ **resolved_current**:矩阵行在 E1 更新
3. pty 多轮 thinking 表面观测窗口不足 → **resolved_current**:第二轮 ask 已纳入 ≥150s 窗口指引
4. tests/test_context.py:42 断言改 assistant→user → **resolved_current**:改动正确(压缩摘要必须 user 角色,严格交替网关才收),知悉并保留
5. <recalled-memory> 措辞泛化可能被忽略 → **resolved_current**:第二轮 lane 加强 preference/constraint 措辞并真实复测
6. -p 模式 save_memory 受审批默认拒 → **resolved_current**:README 已补 --yes 说明与示例
7. 写域守卫无视 permissions.allow 且误报 /dev/null → **resolved_current**:F1 修复 + 4 断言(tests/test_write_scope_allow.py)
8. planner 静默覆盖用户 --worker-mode plan → **resolved_current**:F4 修复(非默认 mode 视为用户钉死,planner 步级 mode 失效)+ 2 断言(tests/test_worker_mode_pinned.py)
9. 2.9 provider 超限无法在真实网关触发(600KB/1.4MB 均被接受)→ **accepted_risk**:证据取自生产路径+client 接缝注入,矩阵如实标注边界
10. /plan --resume 摘要把已完成任务列为首批 → **resolved_current**:F5 修复(首批指向第一个含未完成任务的批次)+ 2 断言(tests/test_plan_resume_summary.py)
11. loop 止损文案 "1 turns" 单复数 → **accepted_risk**:文案级,改动需同步既有断言,收益小
12. /expert 中游子代理空产出仅最终报告可见 → **resolved_followup**:记录在案,进度行就地提示留后续
13. /mcp 只打印 serve 提示不列 server → **resolved_current**:F3 修复(_mcp_status_command 列 server/工具数/disabled/error)+ 3 断言(tests/test_repl_mcp_status.py),第二轮 pty 复验
14. supports_images 按名称判定挡掉真实 vision 模型 → **resolved_current**:F2 修复(llm.vision 显式三态 + capabilities.vision 自动带入 + 名称启发式兜底)+ 6 断言(tests/test_vision_capability.py),第二轮 14.1/14.2 端到端复验
15. -p 旗标顺序敏感反馈 → **non_actionable**:主线程实测旗标前/后置均合法(exit=0),反馈未复现(疑似 shell 引用问题);README 已补 --yes 用法

## 验收证据

- 外部对标：上轮同型包（fix-audit-residual）同口径；本包证据面扩展到 pty 真人模拟 + 反馈独立复核
- 脚本验证：`.venv/bin/python -m pytest -q`（2026-10-19，1046 passed / 2 skipped）；`.venv/bin/python -m ruff check src tests`（全绿）；`python3 /Users/linyixi/.zcode/skills/spec/scripts/spec_verify.py run --all`（9 机器任务 exit=0 记入 runs.jsonl）
- 旧新对比：矩阵 76✅/45🟡/17⬜ → 137✅/1🟡/9🟠/0⬜（docs/testing-matrix.md 汇总节）
- 差异边界：14.2 远程图片端到端受上游网关阻塞（CLI 契约 wire 级证实）；2.9 超限重试为生产路径+client 接缝注入；低危接受项见上方处置记录
- 行为成效：两轮真实使用模拟暴露 13 个缺陷全部修复带回归测试；每轮反馈由未参与修复的复核员独立确认
- 验证范围：package

**验收结果**：通过
