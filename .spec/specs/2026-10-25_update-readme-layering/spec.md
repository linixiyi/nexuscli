# README 分层重排（F1） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 743 行、33 个 H2 的高密度 README 重排为「TL;DR + 特性速览 → 快速上手 → 核心概念 → 深入专题」的分层结构，收敛到 ~400 行（360-440 验收带），内容不丢只重新分层，长实现细节下沉 docs/ 并以带节级锚点的「详见」链接指路
- **目标用户**：第一次接触 NexusCLI 的开发者和需要快速定位细节的现有用户
- **核心价值**： newcomers 能按层渐进读到需要的信息；深度细节在 docs/ 有唯一维护处，README 不再随实现膨胀

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约在 `.spec/plans/2026-10-25_workbench-and-readme/03-readme-restructure.md`（五段定稿：目标结构、33 节逐节下沉映射表、slug 规则、校验面、验收钩子），本包任务由其任务派生提示派生
- README 现状 743 行、33 个 H2；docs/ 现有 13 个 .md；site/build.py 按 `DOC_META`+`PAGER_ORDER` 白名单构建（`site/build.py:26-58`、`site/build.py:351-355`），新 docs 文件不登记就不进站且无报错
- 锚点 slug 规则与 `site/build.py:84-97` 的 `CJKSlugify` 同算法；TUTORIAL.md:282 有 4 个指向 README 节级锚点的链接需同步修补
- site/build.py 顶部 `import markdown` 不是项目依赖，站构建唯一支持命令是 `uv run --with markdown,pygments python site/build.py`（`site/build.py:3-10` 注释）
- pytest 环境经 `tests/conftest.py:19-37` 的 autouse fixture 重定向 HOME/USERPROFILE

### 2.2 关键假设
- 下沉的细节在 docs/ 有着落后，README 删除原文不构成信息丢失（映射表逐行人工核对兜底）
- `site/` 重建产物入库属仓库既有先例，可随本包提交

### 2.3 待确认问题
- 无（规划阶段已由 03 文档与用户决策定稿）

### 2.4 可选解释与取舍
- 当前选择：完整性校验做成 pytest 用例（tests/test_docs_integrity.py）而非独立脚本 -> CI 已跑 pytest，脚本不入 CI 必然腐烂（03「校验面」定稿）
- 当前选择：迁移第 1、2 步（建 docs 文件与登记白名单）同一提交 -> 用例 4 三集合断言在中间态必然红灯（03「数据流·提交纪律」定稿）

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [ ] F1 README 分层重排：TL;DR + 特性速览表 + 三段式结构 + 33 节逐节下沉（7 个新 docs 文件）+ TUTORIAL 链接修补 + 锚点/链接完整性机械断言（对应 master F1）

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- F2-F13（工作台、执行可靠性等，属后续轮次，见 master）
- README 工作台章节（归 F8，第 4 轮）
- README 语义内容的增删（本轮只重排与下沉，不新增能力宣称）

## 4. 最小实现路径
- 最简单可行方案：按 03 映射表移动正文到 7 个新 docs 文件 + 既有 docs 吸收核对 → 登记 build.py → 重写 README → 落地四用例完整性测试收口
- 暂不引入：文档链接检查的第三方工具（markdownlint 等）——自写四用例已覆盖锚点/相对链接/fragment/白名单四类断言
- 不做的抽象/配置化：不把映射表做成配置文件（一次性迁移，人工核对兜底）

## 5. 技术决策
- 技术栈：纯 Markdown + 既有 site/build.py + pytest（零新增依赖）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：README.md、TUTORIAL.md、docs/*.md（7 新建 + permissions.md/subagents.md/其余吸收核对）、site/build.py（两处登记）、tests/test_docs_integrity.py（新增）、site/（重建产物）
- 本轮不应触碰：src/ 全部代码、.github/、pyproject.toml、.spec/plans/ 规划簇（只读）、NEXUS.md
- Git integration branch：`spec/2026-10-25_update-readme-layering`

### 5.1 验证策略
- 范围级别：package
- 变更对象：README.md 及其下沉目标 docs/*.md、文档站构建面、文档完整性测试
- 快速检查：`uv run python -m pytest -q tests/test_docs_integrity.py`（本包新增）
- 集成检查：`uv run --with markdown,pygments python site/build.py`（站点 20 篇产出）+ TUTORIAL 链接修补由用例 2/3 覆盖
- 全项目检查：`uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`（本包不改 Python 运行时，ruff 只守 site/build.py 登记改动）
- 升级触发：不适用（文档包不触共享基础设施；若 site/build.py 登记引入 ruff 回归则就地修复）

### 5.4 编排策略
- route: review（脚本判定依据包结构：风险标签「认证 / 信任边界」，来源 tasks.md 含 permissions 会话权限模式迁移条目）
- 执行决策：迁移与重写是单一所有权的连续内容工作，主线程执行；review 意图落在 check 阶段的独立复审（对照 03 映射表逐节核对内容零丢失）

## 6. 成功标准与验证方式
- [ ] 7 个新 docs 文件建成且既有 docs 吸收核对完成，README 未动时站点构建不报错 -> verify: task_0001
- [ ] site/build.py 白名单登记 7 条，站点构建产出 20 篇 -> verify: task_0002
- [ ] README 重排为三段式分层、收敛到 360-440 行，TUTORIAL.md 链接修补完成 -> verify: task_0003
- [ ] tests/test_docs_integrity.py 四用例全绿（锚点解析/相对链接存在/fragment 命中/行数预算+三集合白名单） -> verify: task_0004
- [ ] 全量 ruff + pytest 回归不破 -> verify: task_0005
- [ ] master F1 勾选、.spec/autorun/ 摘要写入 -> verify: task_0006

## 7. 风险与约束
- 风险点：33 节下沉遗漏或错位 -> 缓解：映射表逐节人工 review（验收钩子第 4 条）+ 用例 3 fragment 断言机械兜底
- 风险点：外链或锚点失效 -> 缓解：用例 1/2/3 三重机械断言
- 风险点：site/build.py 是 Python 文件，登记笔误引入运行时错误 -> 缓解：用例 4 的 ast 解析 + 站点构建 smoke + ruff 覆盖
