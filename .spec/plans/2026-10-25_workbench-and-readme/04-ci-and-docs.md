本文定稿 F8（工作台 CI 与文档收尾）：CI 增加 frontend 构建门禁、新增 docs/workbench.md、README 增加工作台章节并把文档站接通。端点语义以 01 文档为唯一来源、前端工程以前端 detail 文档为准——该文档编号 02（2026-10-25 复审收口：master 细节文档索引已给五份 detail 显式编号，01 对前端的引用已统一改为 02）。本文只做集成与收尾，不复制二者正文。全部结论建立在本轮实读的 .github/workflows/ci.yml、.github/workflows/pages.yml、site/build.py、README.md（含「开发」节）、NEXUS.md、pyproject.toml、.gitignore、tests/conftest.py、docs/runtime-api.md 与 `ls docs/` 实测清单之上。

## 业务逻辑

### F8 的三块交付与动机

1. **CI frontend job**：master 已确认 `frontend/dist` 构建产物允许提交（master.md:32），仓库由此产生一个新的一致性义务——有人改了 `frontend/` 源码却忘了重建 dist 时，仓库里的 UI 与源码脱节，而 Python job（.github/workflows/ci.yml:9-38，ruff + pytest）对此完全失明。frontend job 的存在意义就是这个门禁：`npm ci` + `npm run build` 必须通过，且重建后 dist 无任何漂移——已跟踪文件的改动、删除与**纯新增文件**都算（门禁用 `git status --porcelain`，`git diff` 对纯新增失明，不采用）。**只校验构建通过、不上传产物**：dist 已入库（不需要 artifact 传递）、工作台的部署形态是本地 `serve --workbench`（01:12）而非静态托管、无任何发布目标——upload-pages-artifact 那类产物上传在本仓库唯一对应的 pages.yml:46-49，那是文档站专用。
2. **docs/workbench.md**：工作台横跨 F2-F7 五个特性，端点、构建方式、部署边界散落在实现里；按 NEXUS.md:121-122「长篇设计说明应放到 docs/」，需要一篇用户视角的完整版。它沿用 docs/runtime-api.md:3 的横幅先例（「本文是 README 某章节的完整版」），并在 site/build.py 登记进站。
3. **README 工作台章节**：NEXUS.md:95 要求「行为或公开能力发生变化时，同步更新 README.md，但文档不能先于实现宣称完成」。工作台是公开能力，README 必须有一节；按 F1 重排后的分层规则（03「下沉映射表」规则「同一内容只在一处维护」），README 只留能力宣称 + 启动命令 + 部署边界 + 详见链接，细节下沉 docs/workbench.md。

### 时序与前置

F8 是第 4 轮收尾，前置产物必须已在：F1 的 README 骨架与 tests/test_docs_integrity.py（03「验收钩子」四用例）、F2/F3 的 SSE 端点与 frontend/ 目录（master.md:45-48）、F4-F7 的功能面（master.md:50-59）。由此两条硬约束：

- **完整性测试反转成门禁**：F1 的 test_docs_integrity.py 用例 4 断言 `set(DOC_META) == set(PAGER_ORDER) == {p.stem for p in docs/*.md}`（03 验收钩子·用例 4）。docs/workbench.md 一旦存在，三集合断言立即要求登记 site/build.py——「新建 docs 必须登记」从 review 口号变成红灯，这正是 03「业务逻辑」预言的机制。
- **文档如实宣称**：workbench.md 与 README 的功能描述按 F4-F7 的实际完成度写；某特性若未实现，相应小节留「未实现」状态或整节不写，不得提前宣称（NEXUS.md:95）。

### 与 pages.yml 的关系（部署闭环，无需改动）

文档站部署由独立的 .github/workflows/pages.yml 承担：push main 触碰 `site/**`、`docs/**`、`TUTORIAL.md` 时重建 site/ 并部署（pages.yml:4-10），构建步直接重跑 `python site/build.py` 保证部署页与源同步（pages.yml:31-42）。结论：**workbench.md 进站后，`docs/**` 的路径过滤天然覆盖新文件，pages.yml 零改动**。但要注意 master.md:6 的「autorun 全程只 commit 不 push」——部署发生在用户最终 push 之后，F8 的验收只到「本地重建 + 提交」为止，线上可见性挂账给 push 后人工确认。CI（ci.yml）**不承接**站点构建：完整性已有 test_docs_integrity.py 兜底、部署已有 pages.yml 兜底，再加一层是重复门禁（03 的边界同样未把站点构建塞进 CI）。

## 数据模型

### ci.yml 增量（frontend job 定稿）

现有结构：workflow 级 `on:` 为 push main + 全部 pull_request（ci.yml:3-7），唯一 job `test` 跑 Python 矩阵（ubuntu 3.11/3.12 + windows 3.12，ci.yml:12-19），步骤为 checkout → setup-uv → `uv sync --extra dev --frozen` → ruff 两步 → pytest（ci.yml:20-38）。新增 job 与 `test` **平行、无 `needs` 依赖**（前端构建与 Python 矩阵正交，互不等待；GitHub 默认语义下任一 job 失败即整体红）：

```yaml
  frontend:
    name: Build (Frontend)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install dependencies
        working-directory: frontend
        run: npm ci
      - name: Build
        working-directory: frontend
        run: npm run build
      - name: Check committed dist is in sync
        run: test -z "$(git status --porcelain -- frontend/dist)"
```

逐项定稿依据：Node 版本取 22（仓库已有的 Node 门槛先例是 README:123 的「Chrome DevTools MCP 需要 Node.js 20.19.0 LTS+」，22 满足 Vite 各主线的 20.19+/22.12+ 下限；**若 02 文档定稿的 `engines.node` 高于 22，以其为准——02 未落盘期间本值即定稿，落盘后回核，见挂账⑥**）；`npm ci` 而非 `npm install`（严格按 lockfile 复现。这要求 **package-lock.json 入库**，master.md:47-48 的 F3 只写了提交 dist、未提 lockfile，本文将其定为 F3 的前置要求：缺失则 `npm ci` 与 setup-node 的 cache-dependency-path 双双无法工作，F8 实现时若发现 F3 未满足须先补齐再接 CI，同样记入挂账⑥）；`npm run build` 是门禁（Vite react-ts 模板的 build 脚本默认含 `tsc -b`，类型检查随之进门禁；02 落盘前按模板默认假设，若 02 的脚本不含 tsc，是否单独加类型检查步骤由 02 定稿）；缓存用 setup-node 内建 npm 缓存并锚定 frontend/package-lock.json（Python job 维持现状不加缓存配置，本轮不动它）；最后一步把「提交的 dist 与源同步」变成机械断言：`git status --porcelain` 同时覆盖已跟踪文件的改动/删除与未跟踪的新增文件（`git diff` 不报告未跟踪文件，对「重建只新增了 chunk/资源」的漂移会假绿，故不采用）。**降级条款**：若 master.md:76 的备选成真（用户反对 dist 入库，改空态页方案），删除最后一步，job 退化为纯构建门禁；若实测构建存在无源码改动的 porcelain 噪声（非确定构建），同样删该步并在任务验证记录里如实注明——这是唯一允许的事后放松，不得静默。

### .gitignore 增量（dist 入库的隐形阻塞）

.gitignore:9 的 `dist/` 无前斜杠，匹配**任意层级**的同名目录——`frontend/dist` 默认被忽略，master.md:32「dist 允许提交」在现状下根本落不了库。**载体以 02 定稿为准**（复审修正：02 已定义 `frontend/.gitignore` 忽略 `node_modules/`，根 `.gitignore` 只承担 dist 例外，原「根加 frontend/node_modules/」方案作废）：根 `.gitignore` 追加一行 `!frontend/dist/`（须排在 `dist/` 之后，gitignore 语义为后匹配者胜），`node_modules/` 写进 `frontend/.gitignore`。

**生效时点在 F3 落地 frontend/ 的同一轮，而非 F8**：dist 提交本就是 F3 的验收之一（master.md:48），`npm install` 产生的 node_modules 若不与 frontend/ 同轮被忽略就有入库风险。F8 复核两条在位，并守卫「未被跟踪」——gitignore 对**已跟踪文件无效**，若 F3 轮已把 node_modules 误提交，仅加规则不会取消跟踪，须先 `git rm -r --cached frontend/node_modules` 再随规则一起提交；守卫与回退命令见「验收钩子」第 6 条。

### docs/workbench.md 大纲（定稿）

文件头：H1「# 工作台（Workbench）」+ 横幅「本文是 [README](../README.md)「🖥️ 可视化工作台」章节的完整版。」（横幅章节名必须与 README 实际章节名逐字一致，03 定稿的横幅规则）。正文六个 H2：

| H2 | 内容要点 | 来源 |
| --- | --- | --- |
| H2｜架构 | 「一个 Core、多种入口」定位：REPL/TUI、SDK、MCP、Runtime API + 工作台同为入口；服务端 = 现有 RuntimeApiServer（stdlib `http.server`，零新增 Python 依赖）+ SSE + 静态托管；前端 = `frontend/`（Vite + React + TypeScript），构建产物 `frontend/dist` 由服务端托管 | 01「业务逻辑」、02、master.md:20-23 |
| H2｜快速开始 | 仓库检出可直接用已提交的 dist；自行构建 `cd frontend && npm ci && npm run build`（命令以 02 定稿为准）；启动 `NEXUSCLI_RUNTIME_API_KEY=<key> uv run nexuscli serve --workbench --port 8080`，浏览器打开 `http://127.0.0.1:8080/`；未构建时的空态页行为 | 01:11-12、01:91-92、02 |
| H2｜功能面 | 会话列表与转录回放、任务面板与 usage 统计、Web 审批卡片、Web 发起 turn——逐项按 F4-F7 实际完成度如实写 | master.md:49-59 |
| H2｜端点与 API | 工作台消费的端点表（方法/路径/鉴权/语义/错误码）：SSE 事件流、approvals 查询与决策、静态托管；既有 threads/turns/events/tasks 端点不重复展开，链接 [docs/runtime-api.md](runtime-api.md) | 01「接口与边界」表（01:108-120），抄录为用户文档形态 |
| H2｜部署边界与安全 | 默认硬绑定 127.0.0.1；无多用户认证、不做公网部署（与 MCP server 边界一致）；静态资源免鉴权（无用户数据的构建产物）、`/v1/*` 全走 key；SSE 经 `api_key` 查询参数鉴权的本地风险如实披露；审批超时 300s 失败关闭及其审计口径偏差 | 01:14、01:74、01:95、01:103、master.md:69 |
| H2｜如实边界 | MVP 无前端单元测试（vitest 挂 F8 之后）；dist 已入库但未打进 wheel（01 挂账①），`NEXUSCLI_WORKBENCH_DIST` 可覆盖；Windows 下的前端构建不被 CI 覆盖；截图待补 | master.md:70-71、01:126、本文「接口与边界」 |

写作红线：不引用任何 `.spec/` 路径（spec 计划文档不是产品文档，pyproject.toml:56 也把 `.spec` 排除在维护面之外）；01/02 的正文不复制，只转写成用户视角；文内每条命令必须真实可跑（见验收钩子第 7 条）。

### README 增量（定稿）

在 F1 重排后的骨架（03「目标结构」节定稿的骨架）上做五处增量，全部遵守「同一内容只在一处维护」：

1. **新章节「🖥️ 可视化工作台」**：插入核心概念段「📡 Runtime API」节之后——工作台是 Runtime API 的呈现层，阅读顺序自然衔接；03 骨架的核心概念段为 9 节，插入后为 10 节（03 的行数预算按行计，节序与计数叙述以 03 骨架实数为准）。章节内容（约 12-15 行）：

   ```text
   H2｜🖥️ 可视化工作台
   一句话定位：浏览器里的工作台——会话回放、任务面板、usage 统计、Web 审批与发起 turn（按实现进度宣称）
   启动命令块：NEXUSCLI_RUNTIME_API_KEY=dev-key uv run nexuscli serve --workbench --port 8080 + 打开 http://127.0.0.1:8080/
   构建一行：frontend/dist 已随仓库提交、开箱即用；自行构建 cd frontend && npm ci && npm run build（Node.js 22 LTS + npm）
   ⚠️ 部署边界一行：默认只绑定 127.0.0.1、无多用户认证、不要开放公网
   截图占位注释（方式见下）
   详见链接：docs/workbench.md#部署边界与安全（03 的节级锚点规则：「详见」链接一律带节级锚点；锚点目标=workbench.md 大纲的 H2「部署边界与安全」，无空格无标点，slug 即 `部署边界与安全`）
   ```

2. **截图占位方式——HTML 注释，不放死链**：F1 的 test_relative_links_exist 断言 README 相对链接目标文件存在（03 验收钩子·相对链接存在性用例），`![工作台](docs/workbench.png)` 在图片存在前会挂测试；NEXUS.md:19 禁止假数据/空壳占位，也不能放装饰性假图。定稿：章节内放 `<!-- TODO(截图): 用真实运行的工作台截取一张提交为 docs/workbench.png，然后把本注释替换为图片引用。占位期间不放死链。 -->`；真实截图在 F8 验收 smoke 时人工截取（自动化环境无浏览器则保持注释占位，挂账）。
3. **目录**：核心概念段内「📡 Runtime API」之后加「🖥️ 可视化工作台」一条；锚点按 03 定稿的 slug 规则计算（emoji 与标点剔除，如 `## 🖥️ 可视化工作台` → `#-可视化工作台`），存在性由 test_docs_integrity.py 用例 1 复核。
4. **特性速览表 + 深入文档索引**：各加一行（领域=工作台；深入链接=docs/workbench.md）；深入文档索引从 03 的 20 篇口径变 21 篇。
5. **环境要求 + 开发**：环境要求（F1 保留节）加一行「构建工作台前端需要 Node.js 22 LTS 与 npm（仅自行构建时需要；直接使用已提交的 dist 不需要）」，与 Chrome DevTools MCP 的 Node 先例（README:123）同位；「开发」节（F1 保留 README:708-739 原文）在「运行检查（与 CI 完全一致）」下补一行 `cd frontend && npm ci && npm run build`——否则「与 CI 完全一致」在新 frontend job 落地后名不副实。

行数影响约 +20 行，03 的预算合计从 ~380-400 上浮到 ~400-420，仍落在其 360-440 验收带内（03 的行数验收带）；若逼近上界，压缩工作台节要点密度，不删链接。

### site/build.py 登记条目（定稿）

进站，且必须登记（不登记则站点缺页无报错——site/build.py:351-355 按白名单 `PAGER_ORDER` 迭代，03「业务逻辑」已定论；且 test_docs_integrity.py 用例 4 会直接红）。两处：

- `DOC_META`（site/build.py:26-41）加：`"workbench": ("🖥️", "浏览器可视化工作台：会话回放、实时事件流、任务面板与用量统计")`
- `PAGER_ORDER`（site/build.py:43-57）：在 `"runtime-api"` 之后插入 `"workbench"`（工作台是 runtime-api 的呈现层，阅读相关性最紧；与 F1 新增 7 篇的全局顺序以其定稿为准，本文只锁 workbench 的相对位置）

登记改动是 Python 文件改动，落 ruff 检查面（pyproject.toml:56 只 exclude `.spec`）。最终口径：docs/ 共 21 个 .md（13 既有 + F1 新建 7 + F8 新建 1），站点 21 个 doc 页 + tutorial。README 不经 build.py 渲染（rewrite_links 只改写 docs 渲染页内的 README 链接为 GitHub blob URL，site/build.py:132-160），README 内锚点遵循 GitHub slug 规则即可。

## 数据流

F8 的实现顺序，每步可独立验证：

1. **前置核对**：确认 F1-F7 产物在位（tests/test_docs_integrity.py 存在、`frontend/` 存在且 `npm run build` 可跑、SSE 与审批端点已实现）；缺哪项，workbench.md 对应小节就降级为「未实现」或不写，CI 的 dist 同步步按降级条款处理。
2. **仓库集成复核**：确认 .gitignore 两条（`frontend/.gitignore` 的 `node_modules/`、根 `.gitignore` 的 `!frontend/dist/`——载体以 02 为准，见「数据模型」）已随 F3 在位（生效时点属 F3 轮）；若 F3 轮 node_modules 已被误提交，先 `git rm -r --cached frontend/node_modules`（gitignore 对已跟踪文件无效）；验证 dist 能真正 `git add`——这是 dist 入库模型与 CI 同步检查的共同前提，必须先于 CI 改动。
3. **CI 增量**：ci.yml 加 frontend job（骨架见「数据模型」）；本地跑等价命令（验收钩子第 4、5 条）；CI YAML 无法在本地完整执行，最终语义等首次 push 后看 Actions（挂账项）。
4. **文档流**：01/02 的定稿内容 → 转写为用户视角的 docs/workbench.md（端点表抄录成用户文档、构建命令以 02 为准）→ 登记 site/build.py 两处 → README 五处增量 → 重建站点。方向严格单向：spec 定稿 → 用户文档 → README 摘要，不允许反向（README 先宣称、实现没跟上会违反 NEXUS.md:95）。
5. **提交闭环**：`uv run --with markdown,pygments python site/build.py` 重建 site/（该命令会重写 site/ 下入库产物，属仓库既有先例），docs/workbench.md、ci.yml、.gitignore、site/build.py、README.md、site/ 重建产物一并 commit（autorun 只 commit 不 push，master.md:6）；用户 push 后 pages.yml 因 `docs/**` 触发自动重建部署（pages.yml:4-10），线上可见性在 push 后人工确认一次。

## 接口与边界

### frontend job 的接口语义

| 项 | 定稿 | 理由 |
| --- | --- | --- |
| 触发 | 继承 workflow 级 `on:`（push main + 全部 PR），**不加 paths 过滤** | 构建成本低；全量门禁语义简单；避免「改 ci.yml 自身是否触发」的边界情况 |
| runner | ubuntu-latest，单实例，不进 Python 的矩阵、不含 windows | Node 构建跨平台且被 lockfile 锁定；Python 侧已有 win32 覆盖，前端 Windows 构建不被 CI 覆盖属如实接受的缺口（写入 workbench.md 边界） |
| 门禁 | `npm ci` + `npm run build` + dist 同步检查，任一非零即 job 失败 | 见「业务逻辑」；dist 同步步带降级条款 |
| 产物 | 不 upload artifact、不发布 | dist 已入库；部署形态是本地 serve；无发布目标 |
| 缓存 | setup-node 内建 npm 缓存，锚定 frontend/package-lock.json | 与 checkout@v4 / setup-uv@v5 的既有版本风格一致；Python job 不动 |

### 文档引用边界

- workbench.md 对 01/02 的关系：**实现时以二者定稿内容为唯一来源**，本文只锁大纲与红线；若实现时发现 01/02 与实际代码不一致，以代码为准回改 spec，再同步文档（NEXUS.md 信息优先级第 1 条）。
- README 对 workbench.md：只留宣称 + 命令 + 边界一行 + 链接；端点表、架构细节、边界全清单一律下沉，不在 README 复制（03 下沉映射规则 1）。横幅章节名与 README 章节名逐字一致（03 横幅规则）。
- docs/workbench.md 与 docs/runtime-api.md：既有四组端点不重复展开，只链接；SSE 与 approvals 端点是工作台新面，写全在 workbench.md（其完整用户口径若后续需要独立成文，挂账再议，本轮不拆）。

### 本轮明确不做与挂账

- **不做**：前端单元测试基建（master.md:70-71 明确挂到 F8 之后，CI 因此没有 test 步骤）；CI 产物上传；站点构建进 ci.yml；改动 pages.yml；TUTORIAL.md 增补工作台教程（本轮只保 README/workbench.md 两个入口，教程挂账）；给审计词表等执行器侧任何改动（01:124 已锁）。
- **挂账**：① 真实截图 docs/workbench.png（需人工在真实运行的工作台截取）；② 首次 push 后确认 GitHub Actions 的 frontend job 绿、pages.yml 部署成功（本地无法完整执行 CI）；③ dist 打进 wheel（01:126 挂账①，本文引用不重复定义）；④ workbench.md 的教程化扩充；⑤ 若 02 的 `engines.node` 与 22 不一致，同步 ci.yml 与 README 的 Node 版本行。（原挂账⑥——前端 detail 编号归属——已于 2026-10-25 复审收口：master 索引显式编号 01-05，01 的引用已改 02，02 已落盘可回核本文前提。）
- **任务派生提示（供 /spec:new）**：① .gitignore 两条 + `git check-ignore` 验证；② ci.yml frontend job + 本地等价构建验证；③ site/build.py 两处登记 + 站点重建；④ docs/workbench.md 按大纲撰写 + 完整性测试全绿；⑤ README 五处增量 + 截图注释占位；⑥ smoke：`serve --workbench` 真实启动 + 文档命令逐条跑通。

## 验收钩子

全部命令在仓库根目录执行。F8 无新增 pytest 文件——docs 完整性复用 F1 的 tests/test_docs_integrity.py（四用例见 03「验收钩子」），pytest 环境的 HOME/USERPROFILE autouse 重定向（tests/conftest.py:19-37）照常生效，F8 的用例均为纯仓库文件检查，不触碰 `Path.home()`，无绕开问题。

1. **ruff（覆盖 site/build.py 登记；F8 无其他 Python 运行时改动）**：

   ```bash
   uv run ruff check . && uv run ruff format --check .
   ```

   期望：全绿。pytest 通过数相比 F1-F7 完成后的基线只增不减（F8 本身新增 0 个用例）。

2. **docs 完整性（workbench.md 的登记与链接门禁）**：

   ```bash
   uv run python -m pytest -q tests/test_docs_integrity.py -v
   ```

   期望：4 个用例全绿。用例归属逐一定稿：用例 1 复核 README 新锚点 `#-可视化工作台`；用例 2 复核 README→`docs/workbench.md` 的相对链接目标存在；用例 3 复核 fragment `#部署边界与安全` 命中 workbench.md 的 H2 slug 集合（03 节级锚点规则的机械执行者，无 fragment 的链接落不进它）；用例 4 的三集合断言在 docs/workbench.md 存在后自动强制 site/build.py 登记——若先建文件后登记，中间态该用例红，属驱动信号，其行数断言确认增量后仍在 360-440 带。

3. **全量回归（与 CI Python job 完全一致的三步）**：

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run python -m pytest
   ```

   期望：全部通过。

4. **前端构建本地等价（CI frontend job 的核心两步，F3 落地后执行）**：

   ```bash
   cd frontend && npm ci && npm run build && cd ..
   test -z "$(git status --porcelain -- frontend/dist)" && echo "dist in sync"
   ```

   期望：`npm ci` 不改 package-lock.json、`npm run build` 退出码 0 并产出 dist/、porcelain 输出为空打印 `dist in sync`——已跟踪文件的改动/删除与未跟踪的新增文件都判为漂移（与 CI 同一步，`git diff` 对纯新增失明故不采用）。

5. **CI YAML 语法校验（本地能做的全部，完整语义挂账给 push 后的 Actions）**：

   ```bash
   uv run --with pyyaml python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('ok')"
   ```

   期望：打印 `ok`。`--with` 临时依赖先例同 site/build.py:5-10 的注释约定；pyyaml 不是项目依赖，不进 pyproject.toml。

6. **gitignore 检查（dist 可入库、node_modules 被忽略）**：

   ```bash
   git check-ignore -v frontend/node_modules/cache/x && echo "node_modules ignored"   # 期望命中 frontend/.gitignore 的 node_modules/ 规则（输出形如 frontend/.gitignore:1:node_modules/）
   git check-ignore frontend/dist/index.html || echo "dist NOT ignored"
   git ls-files frontend/node_modules
   ```

   期望：第一条命中 `frontend/.gitignore` 的 `node_modules/` 规则（载体以 02 为准，见「数据模型」）；第二条无命中（退出码非零）并打印 `dist NOT ignored`；第三条**输出为空**。check-ignore 只做规则判定、不管跟踪状态——node_modules 已入库时它照样绿灯（假绿），故第三条的未跟踪守卫不可省；实测本仓库今天 node_modules 无跟踪文件（`git ls-files frontend/node_modules` 为空）、`dist/` 规则确实命中 `frontend/dist/index.html`（exit 0），印证两处都要改。若第三条非空（F3 轮误提交），执行 `git rm -r --cached frontend/node_modules` 后重跑至为空。check-ignore 对不存在的路径同样按规则判定，无需 frontend/ 先就位。

7. **真实 serve smoke（与 01:153-163 的 smoke 合并执行一次；workbench.md 里每条命令逐条跑通——文档命令必须真实可运行）**：

   ```bash
   NEXUSCLI_RUNTIME_API_KEY=dev-key uv run nexuscli serve --workbench --port 8080 &
   curl -sS http://127.0.0.1:8080/ | head -3        # 期望 doctype 文档，大小写不敏感比较（Vite react-ts 模板产出小写 `<!doctype html>`，断言按 lower() 后比较，口径见 02 勘误；dist 在位）
   ```

   期望：启动输出含 `Workbench: http://127.0.0.1:8080/`；首页回 HTML；顺手截取真实工作台截图提交 docs/workbench.png 并替换 README 注释占位（无浏览器环境则保留占位，挂账①）。

8. **站点构建 smoke（build.py 注释规定的唯一支持命令，site/build.py:5-10）**：

   ```bash
   uv run --with markdown,pygments python site/build.py
   ```

   期望：退出码 0；stdout 逐行打印 `docs/<name>.html  <-  docs/<name>.md` 共 21 行（13 既有 + F1 的 7 + workbench）与 `tutorial.html  <-  TUTORIAL.md`；`site/docs/workbench.html` 出现且含 TOC；site/ 重建产物与源文件一并 commit。

9. **人工 review（不自动化，如实边界）**：① workbench.md 的端点表与 01 定稿逐行核对、功能描述与 F2-F7 实际实现一致（不先于实现宣称）；② README 章节名、workbench.md 横幅章节名、深入文档索引行三者章节名逐字一致；③ 截图占位为 HTML 注释且 README 无指向不存在文件的图片链接；④ push 后确认 Actions 的 test / frontend / pages 三 job 全绿（挂账②，本地无法验证）。
