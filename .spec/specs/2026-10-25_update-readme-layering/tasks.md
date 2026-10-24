# README 分层重排（F1） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）
- 迁移任务按 03 文档「数据流·提交纪律」：task_0001 与 task_0002 的产物必须同一提交落盘（用例 4 三集合断言在中间态红灯）

## 阶段一：迁移与登记

- [x] 建 7 个新 docs 文件并完成既有 docs 吸收核对（03 映射表第 1 步）
  - boundary: 只新建 docs/config.md、docs/repl-commands.md、docs/tools.md、docs/skills.md、docs/memory-context.md、docs/plan-execute.md、docs/sessions.md（文件名与定位以 03 映射表为准），并增补 docs/permissions.md（会话权限模式节）与 docs/subagents.md（/fork、/resume 邮箱句）；正文 = README 原文搬移 + 横幅（「本文是 README「XX」章节的完整版」逐字对齐原章节名）+ 读者视角微调；不改 README 本身，不改 site/build.py
  - verify: `uv run --with markdown,pygments python site/build.py`（期望 exit 0 且仍只产出既有 13 篇 + tutorial——新文件未登记不构建也不报错）+ 人工按 03 映射表逐节核对 7 文件内容来源
- [x] 登记 site/build.py 白名单（03 映射表第 2 步，与上一步同一提交）
  - depends-on: task_0001
  - boundary: 只改 site/build.py 的 DOC_META（site/build.py:26-41 加 7 条）与 PAGER_ORDER（site/build.py:43-57 加 7 条，顺序按内容相关性插入）；两处键集合必须一致
  - verify: `uv run --with markdown,pygments python site/build.py && uv run ruff check site/build.py`（期望站点 20 篇全部产出 + ruff 绿）

## 阶段二：README 重写

- [ ] README 重排为三段式分层 + TUTORIAL.md 链接修补（03 映射表第 3 步）
  - depends-on: task_0002
  - boundary: 只改 README.md 与 TUTORIAL.md（TUTORIAL.md:282 的 4 个失效锚点改指 README 新锚点或对应 docs 文件，目标由实现者按重排后结构定，用例 3 机械把关）；README 按 03「目标结构」骨架重排：TL;DR + 特性速览表 → 快速上手 → 核心概念（9 节）→ 深入专题（文档索引 20 篇口径）；下沉小节保留一行「详见 docs/x.md#节」链接（节级锚点按 03 slug 规则）；行数收敛 360-440；不动 NEXUS.md、不动 .github/、不改能力宣称语义
  - verify: `uv run python -c "import pathlib; n=len(pathlib.Path('README.md').read_text(encoding='utf-8').splitlines()); print(n); assert 360 <= n <= 440, n"`（期望打印行数且断言通过）

## 阶段三：机械收口

- [ ] 落地 tests/test_docs_integrity.py 四用例（03「校验面」定稿）
  - depends-on: task_0003
  - boundary: 只新增 tests/test_docs_integrity.py（四用例：README 目录锚点解析、相对链接存在性（README+docs+TUTORIAL，排除 http/mailto/#）、`path.md#frag` fragment 命中目标 slug 集合、行数预算 360-440 + ast 解析 site/build.py 断言 set(DOC_META)==set(PAGER_ORDER)==docs/*.md stems）；纯仓库文件检查不触碰 Path.home()，conftest 重定向照常；不改其他测试
  - verify: `uv run python -m pytest -q tests/test_docs_integrity.py`（期望 4 用例全绿）
- [ ] 全量回归（与 CI 一致的三步）
  - depends-on: task_0004
  - boundary: 不改任何文件，只跑回归；若有失败，修复限于本包 boundary 内文件后重跑
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`（期望全绿）
- [ ] 计划同步：勾选 master F1 + 写 .spec/autorun/ 摘要
  - depends-on: task_0005
  - boundary: 只改 .spec/plans/2026-10-25_workbench-and-readme/master.md（F1 复选框置 [x]）与新增 .spec/autorun/ 摘要记录；不改任何产品文件
  - verify: `grep -c "\[x\] F1 README" .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 1）+ .spec/autorun/ 下存在本包摘要文件
