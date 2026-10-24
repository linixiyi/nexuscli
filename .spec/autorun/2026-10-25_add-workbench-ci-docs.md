# 轮次摘要：2026-10-25_add-workbench-ci-docs（master F8）

## 交付
- ci.yml frontend job：Node 22 + npm 缓存（锚定 package-lock.json）+ npm ci + build +
  porcelain 同步门禁；与 Python test job 平行无依赖；YAML 语法校验过
- docs/workbench.md 六节 + 横幅对齐 README 章节名（不引用 .spec/、不复制 01/02 正文）
- site/build.py 登记 workbench（runtime-api 之后），站点 21 篇产出
- README 五处增量：新章节（含 HTML 注释截图占位 + 详见带节级锚点）、目录、速览表行、
  索引行（21 篇口径）、环境要求 Node 行、开发节前端构建命令；TL;DR 站点计数 20→21

## 证据
- runs.jsonl：task_0001-0005（YAML 校验、21 篇站点构建、完整性四用例、CI 三步全量回归）
- gitignore 复核：frontend/.gitignore node_modules 命中、dist 未被忽略、无跟踪 node_modules
- serve smoke：真实 dist 托管首页 doctype 正常
- CI 语义本地不可完整执行：如实挂账（push 后人工确认 Actions 三 job）

## 简化决策
- 无 Windows 前端构建/Node 矩阵/CI 产物上传（04 边界原文）
