# 工作台 CI 与文档收尾（F8） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：CI 与站点

- [x] ci.yml 增加 frontend job
  - boundary: 只改 .github/workflows/ci.yml（新增与 test 平行的 frontend job：checkout、setup-node 22 + npm 缓存锚定 frontend/package-lock.json、npm ci、npm run build、git status --porcelain 同步门禁）；不改 Python job 与其他 workflow
  - verify: `uv run --with pyyaml python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('ok')"`（期望打印 ok）
- [x] docs/workbench.md 撰写 + site/build.py 登记
  - depends-on: task_0001
  - boundary: 新建 docs/workbench.md（六节：架构/快速开始/功能面/端点与 API/部署边界与安全/如实边界；横幅对齐 README 章节名；不引用 .spec/、不复制 01/02 正文）；site/build.py 的 DOC_META 与 PAGER_ORDER 各加 1 条（runtime-api 之后）；重建 site/
  - verify: `uv run --with markdown,pygments python site/build.py && uv run python -m pytest -q tests/test_docs_integrity.py`（期望 21 篇产出 + 用例 1-3 绿）

## 阶段二：README

- [x] README 五处增量 + 截图占位
  - depends-on: task_0002
  - boundary: 只改 README.md——核心概念「📡 Runtime API」后插「🖥️ 可视化工作台」节（约 12-15 行：定位/启动命令/构建一行/部署边界一行/HTML 注释截图占位/详见链接带节级锚点）、目录两条、特性速览表加一行、深入文档索引加一行（21 篇口径）、环境要求加 Node 行、「开发」节补 frontend 构建命令；TL;DR 文档站计数 20→21
  - verify: `uv run python -m pytest -q tests/test_docs_integrity.py`（期望 4 用例全绿）

## 阶段三：收口

- [x] 全量回归 + serve smoke
  - depends-on: task_0003
  - boundary: 不改产品文件；CI 三步 + gitignore 复核两条 + serve --workbench 首页 smoke + 前端本地等价构建
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master F8 + .spec/autorun/ 摘要
  - depends-on: task_0004
  - boundary: 只改 master（F8 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F8 " .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 1）
