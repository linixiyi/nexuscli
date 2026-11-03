# rag 目录限定检索：OpenViking expr.py 搬运 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 OpenViking `storage/expr.py`（勘察清单【直接可用】，70 行，filter
  表达式）搬进 `src/nexuscli/rag/`，为 `code_index` 检索加上**目录限定**能力——检索结果
  限定在指定子树（如 `src/nexuscli/eval/`），而不是全仓扫描。这是侦察清单「低耦合甜点区」
  最后一件未搬的【直接可用】件。
- **目标用户**：大量代码检索场景的 agent 用户（大仓里限定子目录 = 更准更省）。
- **核心价值**：补齐侦察清单最后一项；检索精度与 token 效率双收益。

## 2. 假设与待确认

### 2.1 已确认事实
- 勘察（archive/2026-10-27_agpl-relicense/evidence/openviking-recon.md）：storage/expr.py
  70 行【直接可用】零内部依赖；耦合面提示：两条依赖轴（RequestContext/viking_cli 类型）
  必须避开——搬运时只取表达式本体
- src/nexuscli/rag/code_index.py：BM25 索引，`search(query, limit)` / `search_with_stats`
  （:136/:140），结果含文件路径——路径过滤的挂载点清晰
- AGPLv3 已生效；直接搬运保留 SPDX 头 + Ported-from commit 9a57854
- 上游仓库未 clone——实现者需先 clone 到 /tmp 只读参考

### 2.2 关键假设
- expr.py 是过滤表达式解析/求值（路径模式匹配为核心语义），可在不引入上游两条依赖轴的
  前提下独立搬运或语义改写
- `search` 加可选 `scope` 参数（子树相对路径），默认 None = 现行为逐字节兼容

### 2.3 待确认问题
- [x] 整搬还是语义改写？→ 结论：实现者读上游源码后按最小 diff 决定——逐字搬运必须
  SPDX + Ported-from；改写则注明语义来源。不预设，结论回写 completion-summary

### 2.4 可选解释与取舍
- 当前选择：scope 语义 = 「结果路径必须位于 scope 子树内」的简单限定，不做复杂布尔
  表达式面 -> 理由：检索场景要的是限定不是查询语言；上游 expr 若含更丰富的过滤语法，
  只搬本项目用得到的路径限定部分
- 当前选择：`search_code` 工具同步加可选 scope 参数 -> 理由：agent 是主要消费者

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] rag 目录限定检索：`search/search_with_stats` 加可选 scope（子树限定，默认 None
  兼容）；`search_code` 工具透传 scope
- [x] 上游 storage/expr.py 搬运（整搬或语义改写，SPDX/来源注记合规）
- [x] 测试：scope 限定（子树内命中/子树外排除/嵌套 scope/None 兼容）、工具透传、
  大小写与路径分隔符归一

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 复杂布尔过滤语法面（只取路径限定）、memory 侧复用、UI 暴露

## 4. 最小实现路径
- 最简单可行方案：rag 新增 expr 模块（搬运）+ code_index 搜索结果路径过滤 + builtins
  search_code 透传；零新依赖
- 暂不引入：scope 的 glob/regex 语法、多 scope 并集
- 不做的抽象/配置化：不做通用过滤表达式框架

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest；上游源码 /tmp clone @ 9a57854 只读参考
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/rag/**、src/nexuscli/tools/builtins.py 的 search_code、
  tests 新增、docs/tools.md
- 本轮不应触碰：memory/**、eval/**、其他工具、索引文件格式
- Git integration branch：`spec/2026-11-03_add-rag-directory-scope`

### 5.1 验证策略
- 范围级别：package
- 变更对象：rag 模块、search_code 工具、rag 测试、docs/tools.md
- 快速检查：`uv run --extra dev python -m pytest -q -k "rag"`
- 集成检查：`uv run --extra dev python -m pytest -q`（search_code 被 agent 广泛使用）
- 全项目检查：适用外；closeout 由发布门禁统一执行
- 升级触发：若必须改索引文件格式才能支持 scope，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：实现由主会话委托动态工作流子代理（用户显式要求）；主会话保留路由、验收、
  done/push 门禁

## 6. 成功标准与验证方式
- [x] scope 限定语义（子树内/外/嵌套/None 兼容）有测试 -> verify: task_0001
- [x] search_code 工具透传 scope + 上游搬运合规注记 + docs/tools.md 同步 + 全量与 Ruff
  双检全绿 -> verify: task_0002

## 7. 风险与约束
- 风险点：路径分隔符/大小写归一不当导致 scope 失灵或误杀 -> 缓解：归一化测试显式覆盖
- 风险点：AGPL 合规瑕疵 -> 缓解：SPDX + Ported-from 双注记；改写注明语义来源
