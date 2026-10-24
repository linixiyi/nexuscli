# 工作台 CI 与文档收尾（F8） - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：CI 增加 frontend 构建门禁（npm ci + build + dist 同步检查）；新增 docs/workbench.md 并登记文档站；README 增加工作台章节
- **目标用户**：贡献者（CI 门禁）与工作台用户（文档）
- **核心价值**：dist 与源码漂移被 CI 拦截；工作台有用户视角文档与 README 入口

## 2. 假设与待确认

### 2.1 已确认事实
- 设计契约：04-ci-and-docs.md（CI job 定稿、workbench.md 六节大纲、README 五处增量、site 登记条目）
- Node 22 为 CI 版本（02 定稿 engines >= 20.19，04 取 22 且 02 未更高）；package-lock.json 已入库（第 2 轮）
- .gitignore 两条（frontend/.gitignore 的 node_modules/、根 !frontend/dist/）已在第 2 轮就位
- docs/ 现有 20 个 .md；tests/test_docs_integrity.py 四用例已由第 1 轮交付

### 2.2 关键假设
- CI YAML 无法在本地完整执行：最终语义等首次 push 后看 Actions（04 挂账②，如实记录）
- workbench.md 声明按 F2-F7 实际完成度写（全部已实现，可完整宣称）

### 2.3 待确认问题
- 无

### 2.4 可选解释与取舍
- 当前选择：dist 同步门禁用 `git status --porcelain -- frontend/dist` 而非 git diff -> git diff 对纯新增失明（04 定稿）

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] F8 工作台 CI 与文档：ci.yml frontend job；docs/workbench.md 六节 + site 登记；README 工作台章节 + 深入索引行 + 环境要求行 + 开发行

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 前端测试基建（master 边界：挂 F8 之后）；CI 产物上传；站点构建进 ci.yml；pages.yml 改动
- TUTORIAL.md 工作台教程（挂账）

## 4. 最小实现路径
- 最简单可行方案：ci.yml 平行 job + build.py 两处登记 + workbench.md 按大纲撰写 + README 增量五处
- 暂不引入：windows 前端构建、Node 矩阵、缓存之外的优化
- 不做的抽象/配置化：Node 版本不做变量注入，写死 22

## 5. 技术决策
- 技术栈：GitHub Actions + 既有文档站构建器
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包 check 门禁）
- 本轮允许改动：.github/workflows/ci.yml、site/build.py（两处登记）、docs/workbench.md（新建）、README.md（五处增量）、site/（重建产物）、frontend/dist（若重建漂移则同步提交）
- 本轮不应触碰：src/、tests/（除既有完整性测试自动覆盖）、pages.yml、pyproject.toml、frontend/ 源码
- Git integration branch：`spec/2026-10-25_add-workbench-ci-docs`

### 5.1 验证策略
- 范围级别：package
- 变更对象：ci.yml、site/build.py 登记、docs/workbench.md、README
- 快速检查：`uv run python -m pytest -q tests/test_docs_integrity.py`
- 集成检查：`uv run --with markdown,pygments python site/build.py`（21 篇产出）+ `cd frontend && npm ci && npm run build`
- 全项目检查：适用外——不改 Python 运行时；与 CI 一致的三步回归已由 task_0005 真实执行并全绿，升级条件见「升级触发」行
- 升级触发：不适用（ci.yml/build.py 语法问题就地修复）

## 6. 成功标准与验证方式
- [x] ci.yml 增加 frontend job（Node 22、npm ci、build、porcelain 同步门禁、npm 缓存）且 YAML 语法校验通过 -> verify: task_0001
- [x] docs/workbench.md 按六节大纲落地（不引用 .spec/、不复制 01/02 正文、每条命令真实可跑） -> verify: task_0002
- [x] site/build.py 登记 workbench（DOC_META + PAGER_ORDER），站点 21 篇产出 -> verify: task_0003
- [x] README 五处增量（新章节/目录/速览行/索引行/环境要求+开发行）与截图占位注释，完整性测试全绿 -> verify: task_0004
- [x] 全量回归（CI 三步）+ serve --workbench smoke -> verify: task_0005
- [x] master F8 勾选、.spec/autorun/ 摘要写入 -> verify: task_0005

## 7. 风险与约束
- 风险点：CI 语义本地不可验证 -> 如实挂账（push 后人工确认 Actions）
- 风险点：README 增量破坏完整性测试 -> 每步跑 test_docs_integrity.py 把关
- 风险点：workbench.md 命令失真 -> 逐条真实执行后才写入
