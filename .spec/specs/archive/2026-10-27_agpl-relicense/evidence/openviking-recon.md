# OpenViking 勘察报告（只读，供 AGPLv3 转换后搬运评估）

勘察者：只读子代理（fresh context，2026-10-26）。/tmp/openviking-src 之外未写任何文件；
未搬运任何代码。

## 基本信息

- 克隆：`git clone --depth 1 https://github.com/volcengine/OpenViking` → /tmp/openviking-src
- commit：`9a57854d51dd598e10a96f4d805268a0f1c16736`（2026-10-26）
- LICENSE：确认 AGPLv3（LICENSE:1-2「GNU AFFERO GENERAL PUBLIC LICENSE / Version 3,
  19 November 2007」）；各 .py 均带 `# SPDX-License-Identifier: AGPL-3.0` 头。
  整仓转 AGPLv3 后许可兼容。

## 布局总览

- `openviking/` 核心 Python 包：retrieve/（检索+context_assembler）、
  session/memory|skill/（记忆/skill 提取更新）、storage/（VikingDB 向量后端 +
  VikingFS + FilterExpr）、core/（URI 命名空间、Context、SkillLoader）、
  models/（embedder/rerank/VLM）、server/（FastAPI）、ingest|parse|scheduling|service/
- `openviking_cli/` CLI 与共享类型；`bot/vikingbot/` 机器人 agent；web-studio/(TS)、
  crates/(Rust)、sdk|examples|tests/
- 重服务化形态：依赖 VikingDB 向量库、embedder/rerank、VLM、Redis（expiry 队列）

## a) memory/ 记忆分层（L0-L2 / TTL / 去重 / 召回）

关键事实：OpenViking 的 **L0/L1/L2 是内容抽象层级**（L0=abstract .abstract.md、
L1=overview、L2=全文），不是「静态项目记忆/长期记忆/会话级」三段
（core/context.py:34-39 ContextLevel、storage/collection_schemas.py:107-116）。
记忆按 URI 命名空间分域（core/namespace.py:9-13,184-191）。

| OpenViking 候选 | 行数 | 职责 | 档位 | 理由/差距 |
|---|---|---|---|---|
| retrieve/memory_lifecycle.py | 63 | hotness 纯函数：sigmoid(访问频次)×指数时间衰减 | 【直接可用】 | 零内部依赖（:10-15）；NexusCLI memory/manager.py:665 可并 hotness 维度 |
| core/context.py:34-39 ContextLevel | — | 三层枚举 | 【直接可用】 | 5 行枚举 |
| retrieve/context_assembler/tiers.py | 230 | 候选按档起 tiers + 预算内加深（:193-230） | 【需适配】 | 依赖 gather.Candidate/params/storage.abstract_overview（:16-29）；本仓无 abstract/overview sidecar |
| retrieve/context_assembler/ 整包 | ~2076 | 检索→预算→分层渲染→跨轮去重（pipeline.py:4-8） | 【需适配】 | budget.py(194)/ledger.py(173) 思路可搬；整体绑定 VikingDB+embedder |
| session/memory/dataclass.py | 547 | MemoryFile/MemoryOperations pydantic 模型 | 【需适配】 | 模型设计可参考；字段面向 LLM 工具协议 |
| session/memory/extract_loop.py 等 | 1506/2116/2323 | VLM ReAct 记忆提取/合并执行器 | 【仅参考】 | 重耦合 VLM+VikingFS+server.identity（extract_loop.py:15-45）；「LLM 产出 MemoryOperations→执行器落盘」模式可借鉴 |
| TTL | — | 无条目级 TTL；scheduling/expiry/ 是 Redis 分布式过期 | 【仅参考】 | 本仓 manager.py:459 SQLite TTL 已满足，勿搬 Redis 栈 |
| 去重 | — | merge_op LLM 驱动合并，无内容哈希去重 | 【仅参考】 | 本仓 manager.py:381 哈希去重已存在且更轻 |

## b) rag/ 目录级限定检索

| OpenViking 候选 | 行数 | 职责 | 档位 | 理由/差距 |
|---|---|---|---|---|
| storage/expr.py | 70 | 过滤 AST：And/Or/Eq/In/Range/PathScope（路径前缀+深度，:62-70） | 【直接可用】 | 纯 dataclass 无依赖；全仓目录 scope 的统一表达 |
| viking_vector_index_backend.py:3061-3115 | ~55 | _build_scope_filter：PathScope Or 组合+level 过滤（:3077-3081「目标已窄于可见根则降为 Eq」优化） | 【需适配】 | 核心就 55 行；嵌在 3000+ 行类里，建议重写为 SQLite path LIKE 'dir/%' |
| core/retrieval_targets.py | 199 | target_uri 解析→去重目录集；按 ContextType 给默认目标目录（:46-77） | 【需适配】 | 依赖 namespace.py+server.identity；「默认目录集」对本仓 code_index root 限定有参考价值 |
| retrieve/hierarchical_retriever.py | 407 | 稠密+稀疏向量检索、rerank、URI 去重取最高分（:237-249） | 【仅参考】 | 绑定 VikingDB/embedder/rerank；本仓是 SQLite BM25（rag/code_index.py:103-159），无向量栈 |

## c) skill/ 多信号检索

| OpenViking 候选 | 行数 | 职责 | 档位 | 理由/差距 |
|---|---|---|---|---|
| core/skill_loader.py | 270 | SKILL.md frontmatter 解析/校验/序列化（纯 re+yaml，:5-11） | 【直接可用】 | 零内部依赖；本仓 skill/registry.py:18-35 可用它替换自写解析并获得校验规则 |
| bot/vikingbot/agent/skills.py | 231 | 本地 SkillsLoader：双目录、requirements(bins/env) 可用性过滤（:182-191）、always-on（:198-206）、XML 摘要渐进加载（:104-143） | 【需适配】 | 依赖 vikingbot.utils.helpers；requirements 检查与「渐进披露」值得搬；但无 Top-K 匹配 |
| retrieve/skill_package_retriever.py | 120 | skill 向量检索（:60-79） | 【仅参考】 | 继承 HierarchicalRetriever，依赖 embedder+VikingDB |
| retrieve/intent_analyzer.py | 191 | LLM 生成多类型查询计划 | 【仅参考】 | 需 VLM |
| 多信号匹配 | — | **OpenViking 无对应物**（只有向量语义一路）；名称/描述/内容多信号加权 Top-K 反而是本仓已有（skill/registry.py:57-68） | 【无】 | 此面 NexusCLI 领先，无货可搬 |

## 耦合面提示

1. 两条依赖轴贯穿全仓：server.identity.RequestContext（租户/ACL）与
   openviking_cli.* 类型（核心包反向依赖 CLI 包，core/context.py:12-13）——搬任何
   retrieve/storage 文件都会拖进这两串。
2. 向量栈是隐性前提：不引入向量库时 b/c 面只能搬 expr.py + scope 过滤思想。
3. 记忆提取侧是服务形态（~6000 行，依赖 VikingFS/VLM/merge_op），与进程内 SQLite
   模型不兼容——只搬「LLM 产出结构化操作→执行器合并落盘」的模式。
4. 低耦合甜点区（搬运性价比最高）：storage/expr.py(70)、
   retrieve/memory_lifecycle.py(63)、core/skill_loader.py(270)、ContextLevel 枚举；
   其次 retrieval_targets.py(199) 与 tiers.py(230)。
5. bot/vikingbot/agent/skills.py:1 无 SPDX 头，但仍在 AGPLv3 仓库内受许可约束。
