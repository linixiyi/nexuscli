# provider 细节与 mypy 纪律（F12/F13） - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：并行实现（子代理 /workflow 派发）

- [x] F12 Anthropic provider 细节对齐
  - boundary: 只改 src/nexuscli/llm/anthropic.py、src/nexuscli/agent/agent.py（盘点表定位点：react 循环
    「无工具调用才收尾」分支）、tests/test_anthropic_client.py；按 05 F12 盘点表四项补齐并补核实测试；
    不动 OpenAI-compatible 路径
  - verify: `uv run python -m pytest -q tests/test_anthropic_client.py`
- [x] F13 mypy 渐进接入 CI
  - boundary: 只改 pyproject.toml（或 mypy.ini）+ 新增排除清单 + .github/workflows/ci.yml（mypy job 或
    并入现有步骤）；排除清单按实测摸底生成、只减不增；新改动模块（llm/anthropic.py、agent/results.py、
    agent/turn_checkpoint.py、tools/journal.py、runtime/approvals.py、runtime/reads.py、net_guards.py）
    必须通过；YAML 语法校验
  - verify: `uv run --with mypy mypy src/nexuscli`

## 阶段二：收口

- [x] 全量回归（CI 三步）
  - depends-on: task_0001, task_0002
  - boundary: 不改产品文件；CI 三步；失败修复限于本包 boundary
  - verify: `uv run ruff check . && uv run ruff format --check . && uv run python -m pytest`
- [x] 计划同步：勾选 master F12/F13 + .spec/autorun/ 摘要
  - depends-on: task_0003
  - boundary: 只改 master（F12/F13 置 [x]）与 .spec/autorun/ 摘要
  - verify: `grep -c "\[x\] F1[23] " .spec/plans/2026-10-25_workbench-and-readme/master.md`（期望输出 2）
