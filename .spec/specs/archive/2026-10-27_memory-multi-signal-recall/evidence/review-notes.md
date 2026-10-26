# check 轮发现与处置（2026-10-27，独立评审员 3 条 + 主会话核实）

## R1 分支上 LICENSE 仍 MIT 而搬运件是 AGPL（合并顺序约束激活）
- 评审员报 unconfirmed；主会话核实：分支基 main（MIT），memory/memory_lifecycle.py
  带 AGPL-3.0 SPDX 头——正是 spec §4 预告的合并顺序约束。
- 处置：**accepted_risk**（按设计）：本分支必须后于 2026-10-27_agpl-relicense 合入
  main；spec §4 已明文，push 阶段按 workbench → eval-harness → agpl-relicense →
  memory-recall 顺序合并即可消除窗口。

## R2 VectorSignal 无 config 开关（spec §3.1 字面 vs §4 禁 config 化冲突）
- 评审员报 unconfirmed；主会话核实：config.py 无 vector 字段；注入走
  MemoryManager(signals=[...VectorSignal(embedder)]) 程序化位；「默认缺席三路照常」
  有测试（test_memory_signals.py:212-237）。
- 处置：**resolved_current**：按 §4「不做 config 化」裁决，§3.1 字面在此勘误为
  「程序化注入位，默认缺席」——成功标准不变（该标准本就是缺席语义）。

## R3 同 hash 重复到达实体并集无上限（存储无界增长）
- 评审员报 unconfirmed；主会话核实属实：_entities_to_json 无帽，多次不同实体列表
  的 save 按 32×到达次数增长（打分不受影响，纯存储膨胀）。
- 处置：**resolved_current**：_entities_to_json 截断 [:MAX_ENTITIES]（排序确定性
  截断），signals 的 _MAX_ENTITIES 转正为公共 MAX_ENTITIES；memory 65 测试全绿 +
  ruff 双绿复跑通过。

## 实现报备事项核可
- tests/test_eval.py 最小同步（3→4 内置场景）：不在 §5 允许清单但新增 04 场景的
  必然连带，最小改动，核可（与 eval-harness-p1 包同款先例）。
- eval 场景 stub save_memory requires_approval=false：eval runner 无审批回调，
  真实工具会 fail-closed；偏差写进工具 description，核可。
- 断言词汇用 expected_tool_sequence（loader 现有词汇，spec §2.3 已预期）。
