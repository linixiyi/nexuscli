# Mem0 式多信号记忆召回 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 src/nexuscli/memory/manager.py 的纯词法召回（_relevance_score：
  query 词覆盖率 + 子串命中，权重 0.72/0.12/0.08/0.06/0.02）改造成多信号融合召回：
  词法 + BM25 + 实体三路并行打分加权，叠加时间感知分层（当前状态/过去事件/未来计划）。
- **目标用户**：依赖 save_memory/search_memory 与 prompt 自动召回的 agent 用户。
- **核心价值**：目标条目在对抗性查询下进前 K（纯词法会被同词面噪声挤掉的场景），
  召回排序可解释、可评测。

## 2. 假设与待确认

### 2.1 已确认事实
- 召回链：search_memory 工具（builtins.py:849-872）→ MemoryManager.recall
  （manager.py:167-231）→ _relevance_score（:665-688）；prompt 自动召回同走 recall
  （prompt/assembler.py:138-148）
- 数据模型无 entities/metadata 列、无事件时间；kind 五值 fact/preference/constraint/
  correction/decision；「当前/过去/未来」无任何现有表达
- BM25 在 rag/code_index.py:160-180 是 @staticmethod 纯函数（k1=1.5 b=0.75，
  idf=log(1+(N-df+.5)/(df+.5))），不依赖 self/DB——可提升为模块级公共函数供 memory
  以「内存现算 batch 统计」方式复用，零索引文件耦合
- 现状 save() 是 upsert（同 content_hash 覆盖 kind/source、刷新 updated_at、
  importance/confidence 取 max），非 ADD-only
- 去重 = sha256 归一化 content 唯一索引 (scope, content_hash)；TTL = expires_at 同步
  物理清除；quota = 1000/scope 淘汰序 importance→confidence→access→updated→id
- /goal 存独立 JSON（~/.nexuscli/goals/），与 memory.db 完全分离，每轮重注入
- OpenViking（AGPLv3，commit 9a57854）retrieve/memory_lifecycle.py：hotness 纯函数
  （sigmoid 访问频次 × 指数时间衰减，63 行，零内部依赖，带上游版权头）——
  agpl-relicense 包勘察判定【直接可用】；本仓已转 AGPL-3.0-only，搬运合法
- 2026-10-27_eval-scenario-types 包**不存在**（全分支/归档核查无）

### 2.2 关键假设
- BM25 对小 batch（scope 内记忆条数 ≤1000）内存现算的性能可接受（无索引）
- 实体抽取不引 NLP 依赖：save_memory 工具加可选 entities 参数（LLM 供给）+
  无参数时轻量启发式兜底（引号词/连续大写词/已知名词模式）
- 查询意图（时态加权）用中英文关键词表判别即可，不需要 LLM 参与

### 2.3 待确认问题
- [x] eval-scenario-types 包不存在的验收依据问题？→ 结论：本包自带记忆召回评测场景
  （examples/evals 新场景 + 单测 top-K 断言 + 对抗场景），若该包日后落地再对齐迁移；
  不阻塞本轮
- [x] 「写路径保持 ADD-only」与现状 upsert 的落差？→ 结论：按用户指令「只累积不覆盖」
  落实为最小收窄：同 hash 到达不再覆盖 kind/source/expires_at（只累积不变更既有声明），
  importance/confidence 取 max、updated_at/access_count 照常刷新（访问信号非内容覆盖）；
  去重/TTL/quota 语义不动

### 2.4 可选解释与取舍
- 当前选择：temporal_kind 三值（current_state/past_event/future_plan）为新列 + schema
  迁移，旧行按 kind 映射回填（preference/constraint/decision→current_state，
  fact→past_event） -> 不破坏既有库；未来计划由 save_memory 的显式参数或启发式标注
- 当前选择：时间衰减搬运 OpenViking hotness_score（保留上游版权头，Ported-from 注明）
  而非重写 -> agpl 包已判定直接可用，搬运合法且省审校成本
- 当前选择：时态加权 = 查询意图判别（关键词表）× 条目 temporal_kind 匹配乘数 ->
  不引入 LLM 调用，保持 hermetic 与确定性

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 多信号融合：词法（现有逻辑保持）+ BM25（batch 内存现算，bm25_score 提升为
  公共纯函数）+ 实体匹配（entities 列 + save_memory 可选参数 + 轻量启发式）三路
  加权融合进 _relevance_score；信号源做成可替换接口（MemorySignal），BM25 不耦合
  rag 索引文件
- [x] 可选向量信号插桩：MemorySignal 接口留 vector 实现位（config 开关默认关），
  无向量时三路照常，不引入强制依赖
- [x] 时间感知分层：temporal_kind 列（current_state/past_event/future_plan）+ 旧行
  迁移回填 + event_at 事件时间列 + 查询意图时态加权 + OpenViking hotness 搬运用于
  衰减信号
- [x] 写路径 ADD-only 收窄：同 hash 不覆盖 kind/source/expires_at；治理语义
  （去重/TTL/quota）不变
- [x] 评测与对抗：单测 top-K 命中断言（含「多信号相对纯词法改善」的对抗用例）+
  examples/evals 新增记忆召回端到端场景（现有断言词汇）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 不引入 mem0 依赖、不引入向量库/embedding 依赖
- /goal 并入 memory 存储（保持独立 JSON，仅时态加权时把 goal 视为 current_state
  语境信号，不改存储）
- subagent 文件制记忆（agent-memory/）改造
- prompt 自动召回的分块展示重构（assembler 只做最小适配）

## 4. 最小实现路径
- 最简单可行方案：signals.py（MemorySignal 协议 + lexical/bm25/entity/temporal 四个
  实现）+ manager._relevance_score 改为信号融合 + schema 增列迁移（参照既有 alter
  模式）+ builtins 工具 schema 扩参
- 暂不引入：向量/embedding、LLM 实体抽取、查询改写、按信号分块展示、跨 scope 召回
- 不做的抽象/配置化：权重不做 config 化（代码内常量+单测锚定）、不做信号注册表
  （固定四路+vector 留位）
- 合并顺序约束：本分支引入 OpenViking AGPL 代码，必须后于 2026-10-27_agpl-relicense
  合入 main（否则 main 仍是 MIT 时出现许可不一致窗口）

## 5. 技术决策
- 技术栈：纯 Python 标准库（math/sqlite3），无新依赖
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 verify: manual: <理由>；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/memory/**、src/nexuscli/rag/code_index.py（仅 bm25_score
  提升为模块级公共函数）、tools/builtins.py 的 save/search_memory 工具面、
  prompt/assembler.py 最小适配、entrypoints/repl.py /save 参数面、examples/evals/
  新增场景、tests/test_memory*.py、docs/memory-context.md、README 记忆行
- 本轮不应触碰：去重/TTL/quota 语义、/goal 存储、search 返回格式（#{id} 行式保留，
  只增不改）、既有四种+新增 eval 断言语义、code_index 索引存储
- Git integration branch：`spec/2026-10-27_memory-multi-signal-recall`

### 5.1 验证策略
- 范围级别：package
- 变更对象：memory 域、工具面、eval 场景、文档
- 快速检查：`uv run python -m pytest tests -q -k memory`
- 集成检查：`uv run nexuscli eval run --file examples/evals --cwd /tmp/nexuscli-mem-eval`
  （新增记忆场景全绿）+ 真实入口冒烟（task_0005 双连跑）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（memory 域内
  改造 + 工具参数面增量）；全量测试由集成检查自愿覆盖，由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或
  package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] 三路信号融合落地且 top-K 单测含对抗改善用例（多信号能召回纯词法排不进的条目）
  -> verify: task_0001
- [x] 时间分层落地：temporal_kind 迁移回填 + 查询意图加权有测试 -> verify: task_0002
- [x] 可插拔接口成立：vector 信号位默认缺席三路照常 + BM25 无 rag 索引耦合
  -> verify: task_0001
- [x] examples/evals 记忆召回场景经真实 eval 入口全绿 -> verify: task_0003
- [x] 文档联动（docs/memory-context.md + README 记忆行）-> verify: task_0004
- [x] 全量 pytest + ruff + 真实入口冒烟（save 后 recall 命中）-> verify: task_0005

## 7. 风险与约束
- 风险点：融合权重拍脑袋导致排序回归 -> 缓解：现有 8 例 memory 测试全保 + 新增
  对抗用例锚定改善方向 + 权重常量单测断言
- 风险点：schema 迁移破坏既有 memory.db -> 缓解：沿用既有 user_version 门控 alter
  模式（manager.py:352-432 先例），迁移测试覆盖旧库升级路径
- 风险点：OpenViking 搬运件与上游漂移 -> 缓解：Ported-from 注明 commit sha，
  单测锚定 hotness 边界值
