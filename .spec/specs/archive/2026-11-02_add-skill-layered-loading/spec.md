# Skill L0/L1/L2 分层加载 + real 轨 load_skill 白名单修复 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 skill 上下文从两级（候选行 → load_skill 全文）升级为 L0/L1/L2 三层
  （abstract → overview → 全文，OpenViking 内容抽象层级思路），修复 real 轨 skill 注入
  「半开」（候选开、load_skill 被 deny）的不一致，并给出真实轨道的 skill 行为场景。
- **目标用户**：skill 作者（省 token 的分层表达）、真模型 nightly 轨道（skill 规则可达）、
  后续 propose 闭环（依赖本包的可达性）。
- **核心价值**：候选阶段信息更准（abstract）、按需加载省 token（L1）、skill 进化规则在
  真实轨道可被模型读到并遵守（闭环 B 的前置条件）。

## 2. 假设与待确认

### 2.1 已确认事实（2026-11-02 审计工作流 file:line 证据）
- Skill 数据类只有 description/body（skill/registry.py:17-35），frontmatter 自写解析
- 注入两级：top-5 候选行 name+description+tags（agent.py:748-766）→ load_skill 全文
  （tools/builtins.py:359）
- real 轨半开：REAL_TRACK_TOOLS 13 项无 load_skill（real.py:82-98），白名单外被 deny
  （real.py:395），而 features.skill 默认 True → 候选注入生效但全文不可达
- OpenViking 勘察（archive/2026-10-27_agpl-relicense/evidence/openviking-recon.md）：
  core/skill_loader.py（270 行，SKILL.md frontmatter 解析/校验/序列化，纯 re+yaml 零内部
  依赖）【直接可用】；bot/vikingbot/agent/skills.py 的 requirements(bins/env) 可用性过滤
  （:182-191）与渐进披露【需适配】；skill 多信号匹配本仓已领先【无】
- AGPLv3 已生效（2026-10-27_agpl-relicense），搬运合法，须保留 SPDX 头 + Ported-from commit
- hermetic eval 运行 features.skill=False（runner.py:353），本包不影响封闭基线
- L1 生成来源：frontmatter `overview:` 显式声明优先，缺省从 body 首段自动截断并标注 generated

### 2.2 关键假设
- load_skill 内容面只读（按名字在固定 skill 根解析，无路径参数、不可遍历），当初被
  白名单排除是按「用户态写」误伤——加入白名单后 real 轨候选 → load_skill 全文链路
  完整且安全。【实现期修正 2026-11-02，独立评审指出】原假设「无落盘副作用」不成立：
  注入 seam 的 `SkillStateStore().record_use` 会写 `~/.nexuscli/usage.json` 用量计数器
  （sibling of skills.json）。接受口径：该写是记账性质（无任何行为决策读它），real 轨
  不变量由「unattended 零写」显式收窄为「零工作区写 + 此一处已声明的用户目录计数器写」，
  区别于 search_memory 的行为态写（UPDATE access_count 改召回、DELETE 过期行）——后者
  继续排除
- load_skill 的 `level` 参数默认 full 保持向后兼容，现有用户与场景零破坏
- skill 规则遵循在真模型上高度可达但非 100%（real-readonly-converge 的 bash 教训）——
  real 场景验收判结构可达性（链路 + 判定发生），PASS/FAIL 是 nightly 行为信号

### 2.3 待确认问题
- [x] skill_loader.py 整搬还是取校验规则？→ 结论：实现者按最小 diff 决定——本仓解析
  能扩展则只搬其校验规则与错误文案风格；整搬必须保留 SPDX 头 + Ported-from 并适配
  Skill 数据类，结论回写 completion-summary

### 2.4 可选解释与取舍
- 当前选择：L1 用 frontmatter 显式声明优先 + body 首段自动截断兜底，不建 .overview.md
  旁挂文件 -> 理由：单文件自包含，避免双源真相
- 当前选择：候选行 description 有 abstract 时被 abstract 替换 -> 理由：abstract 语义就是
  「给候选阶段的更强一句话」，二者并存浪费 token
- 当前选择：requirements 过滤只做 bins（PATH 可执行）与 env（环境变量）两类 -> 理由：
  recon 原语义就是这两类；缺失 skill 不进候选但 `/skill list` 可见并标 unavailable

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] Skill 三层：registry 解析 frontmatter 可选 `abstract`/`overview` 字段 + L1 自动生成
  兜底；`/skill list`/`load_skill` 返回层信息
- [x] `load_skill` 加可选 `level`（full 默认/overview），向后兼容；候选行 abstract 替换
- [x] real 轨修复：load_skill 入白名单；real skill 行为场景 03（规则含可观测标记，
  候选 → load_skill → 遵循链路，final/judge 断言）
- [x] requirements 可用性过滤搬运（bins/env 两类，unavailable 不进候选、/skill list 标注）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- XML 渐进披露、.abstract.md 旁挂文件、向量检索（recon 标仅参考）
- hermetic eval 的 skill 注入开关（保持 False）
- save_skill 写 abstract/overview 的表单化（文档说明手写即可）

## 4. 最小实现路径
- 最简单可行方案：registry 解析扩字段 + 两个调用点（agent.py 候选行、builtins.py
  load_skill）+ real.py 白名单一行 + 一个 real 场景 JSON + requirements 过滤小函数
- 暂不引入：skill 内容哈希失效检测、L1 缓存、多 skill 组合加载
- 不做的抽象/配置化：不做「层级策略」接口；不抽象通用 frontmatter 校验框架

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest；搬运件保留 AGPL 头
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/skill/**、src/nexuscli/agent/agent.py 候选行、
  src/nexuscli/tools/builtins.py 的 load_skill、src/nexuscli/eval/real.py 白名单、
  src/nexuscli/builtin_skills/、examples/evals/real/、tests 新增、docs/skills.md、
  docs/tools.md load_skill 段、README Skill 行。【实现期修正 2026-11-02，独立评审两轮
  指出清单漏列，与实际改动集对齐】补列：src/nexuscli/entrypoints/commands/skill.py
  （/skill list 层信息与 requirements unavailable 标注——§3.1 点名功能的实现所在）、
  docs/eval.md（real 轨段落与白名单新口径同步）、仓库根 .agents/skills/
  （场景 03 依赖的 rule-demo fixture，registry 扫描的项目级标准目录且不被 gitignore）
- 本轮不应触碰：eval loader/runner/reporter、memory、telemetry、其他工具与 CLI 命令
- Git integration branch：`spec/2026-11-02_add-skill-layered-loading`

### 5.1 验证策略
- 范围级别：package
- 变更对象：skill 模块、agent 候选行、load_skill、real 白名单、real 场景、两篇 docs
- 快速检查：`uv run --extra dev python -m pytest -q -k "skill or eval"`
- 集成检查：`uv run --extra dev python -m pytest -q`（agent 候选行与 builtins 被多处
  引用，全量兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；closeout 由发布
  门禁统一执行
- 升级触发：若必须动 eval loader/runner 或工具执行器门链才能完成，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：实现段由主会话委托动态工作流子代理完成（用户显式要求），真实网关冒烟
  （task_0004）刻意不在工作流——AGENTS.md「执行环境与冒烟」守则，由 spec_verify 在
  主会话执行；主会话保留路由、验收、done/push 门禁

## 6. 成功标准与验证方式
- [x] 三层解析/生成/层信息/level 参数/候选行 abstract 替换有测试 -> verify: task_0001
- [x] real 轨 load_skill 可达（白名单测试）+ 场景 03 落地 -> verify: task_0002
- [x] requirements 过滤（bins/env，unavailable 标注）有测试，docs 两篇 + README 同步，
  全量与 Ruff 双检全绿 -> verify: task_0003
- [x] 真实入口跑场景 03：链路可达且判定发生（报告结构断言；模型遵循与否是 nightly
  行为信号不是本门禁） -> verify: task_0004

## 7. 风险与约束
- 风险点：候选行替换 abstract 使依赖 description 的匹配/测试变化 -> 缓解：abstract 缺省
  回落 description，Top-K 匹配仍用原字段
- 风险点：load_skill 进 real 白名单被滥用读敏感文件 -> 缓解：skill 目录白名单校验既有
  （_validate_skill_slug），PathGuard 照常生效；文档明示
- 风险点：搬运件许可证瑕疵 -> 缓解：SPDX + Ported-from commit 双注记，recon 确认上游
  文件在 AGPLv3 仓库内
