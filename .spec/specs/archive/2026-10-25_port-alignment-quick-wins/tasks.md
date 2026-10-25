# 对齐第 1 轮：零耦合小件照搬（A1-A4） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：照搬实现（子代理 /workflow 三 lane 并行）

- [x] A1+A2 lane：HTTP 安全壳 + 写端点幂等门（api.py 串行实现）
  - boundary: 只改 src/nexuscli/runtime/api.py（统一响应头出口、_handle 前置 413/415 门、
    幂等登记表 + decision/turns/threads 等写端点 request_id 门）+ 新增 tests/test_runtime_hardening.py；
    kitagent 出处注释（web/app.py:106-128、web/gateway.py:72-93）
  - verify: `uv run python -m pytest -q tests/test_runtime_hardening.py tests/test_runtime_websession.py tests/test_runtime.py`
- [x] A3 lane：文档鉴权滞后修复 + 完整性断言
  - boundary: 只改 docs/workbench.md、docs/runtime-api.md（补 bootstrap/csrf/cookie 鉴权节，同步 F14 语义）
    与 tests/test_docs_integrity.py（新断言：runtime 文档覆盖鉴权关键词）
  - verify: `uv run python -m pytest -q tests/test_docs_integrity.py`
- [x] A4 lane：事件日志 repair_tail 移植
  - boundary: 只改 src/nexuscli/session/（写路径增 fsync+校验选项）与 src/nexuscli/runtime/reads.py
    （read_transcript 损坏尾截回 + 严格行校验开关）+ 新增 tests/test_event_log_integrity.py；
    kitagent 出处注释（core/graph/event_log.py:29-50）；正常路径零变化
  - verify: `uv run python -m pytest -q tests/test_event_log_integrity.py tests/test_runtime_reads.py`

## 阶段二：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0001, task_0002, task_0003
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master A1-A4 + .spec/autorun/ 摘要
  - depends-on: task_0004
  - boundary: 只改 master（A1-A4 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] A[1-4] " .spec/plans/2026-10-25_kitagent-alignment/master.md`（期望输出 4）
