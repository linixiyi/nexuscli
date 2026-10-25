# 对齐第 5 轮：报告导出与协议文档链（A13/A14） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：并行实现（子代理 /workflow 两 lane；文件面不相交）

- [x] A13 lane：工作台运行报告导出
  - boundary: 只改 frontend/src/views/TranscriptView.tsx（导出按钮）与
    frontend/src/views/report.ts（新：transcript+usage 组装 markdown 纯函数，含
    「模型结论不代替检查证据」声明）与 frontend/dist（重建）；不改 Python 侧
  - verify: `cd frontend && npm run build`
- [x] A14 lane：端点文档生成链
  - boundary: 只改 src/nexuscli/runtime/api.py（抽 ROUTE_MANIFEST 常量元组：method/path/鉴权/语义摘要，
    路由分发逻辑零变化）+ scripts/generate_endpoint_docs.py（从常量生成 docs/runtime-api.md 的
    端点表节，节用 Do-not-edit 围栏）+ scripts/check_endpoint_docs.py（生成内容与文档比对，不同步非零退出）
    + docs/runtime-api.md（端点表节改为生成产物）+ .github/workflows/ci.yml（加同步校验步骤）
  - verify: `uv run python scripts/generate_endpoint_docs.py && uv run python scripts/check_endpoint_docs.py`

## 阶段二：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0001, task_0002
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master A13/A14 + .spec/autorun/ 摘要
  - depends-on: task_0003
  - boundary: 只改 master（A13/A14 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] A1[34] " .spec/plans/2026-10-25_kitagent-alignment/master.md`（期望输出 2）
