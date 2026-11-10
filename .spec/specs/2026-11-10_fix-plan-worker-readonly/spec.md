<!-- spec-format: 3 -->

# Plan 模式 worker 只读约束 - 项目范围

## Goal

- goal: 让 `--mode plan` 的 worker 在运行时只看到只读工具，并在系统提示中明确 plan 只读；纯只读目标不再因 worker 调用 `bash`/`write_file` 而在首批任务被 blocked。
- literal ask: 「你写 /spec 包，我让 zcode 来执行」——针对复查发现的 plan 模式 worker 未受只读指令约束问题。
- stated purpose: 把 planner 已有的只读白名单从“提示层”下沉到 worker 的运行时工具表，消除 plan 模式对只读目标也必然 blocked 的可用性缺陷。
- done means: plan 模式 worker 的 `ToolRegistry` 只包含 `read_file`/`glob`/`grep`/`search_code`/`vfs_ls`/`vfs_read`；worker 系统提示明确 plan 只读并禁止 bash/write_file；相关单测与文档契约测试通过；真实 `--mode plan` 冒烟不再出现 worker 首批调用 bash/write_file。

## Charter

- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md

## Scope

- in: src/nexuscli/agent/plan_execute.py, tests/**, README.md, docs/plan-execute.md
- out: src/nexuscli/tools/executor.py, src/nexuscli/policy/**, .github/**, pyproject.toml, uv.lock

## Assumptions

- A1 | worker 执行路径把完整 `self.tool_registry` 传给 `query()`，plan 模式没有过滤 | fact | probe: `sed -n '500,510p' src/nexuscli/agent/plan_execute.py` | CONFIRMED | if false: 重新定位 worker 工具注入点
- A2 | `_task_system_prompt()` 用完整 `self.tool_registry.list_names()` 构建提示，且没有 plan 只读约束 | fact | probe: `sed -n '571,585p' src/nexuscli/agent/plan_execute.py` | CONFIRMED | if false: 改为在真实提示构建处注入
- A3 | `ToolRegistry` 提供 `get()`、`list_names()`、`register()`，可安全构造子集 | fact | probe: `sed -n '25,55p' src/nexuscli/tools/registry.py` | CONFIRMED | if false: 改用 registry 现有 API 或直接过滤 `definitions()`
- A4 | 现有 plan 测试已覆盖执行层只读闸 blocked，不覆盖 worker 可见工具表 | fact | probe: `grep -n "registry_with_write_tool\|gate_rejection_blocks_task" tests/test_plan_readonly.py` | CONFIRMED | if false: 复用已有覆盖并调整新测试边界
- A5 | 过滤 worker registry 后，模型仍可能编造工具名，但执行层会继续 blocked | assumption | UNVERIFIED | if false: 保留执行层 blocked 兜底，并加强 worker prompt 与测试断言
- A6 | README 与 docs/plan-execute.md 当前已声明“六个只读工具”，但这是 planner prompt 语义，不是 worker 运行时强制 | fact | probe: `grep -n "六个只读工具\|read_file/glob" README.md docs/plan-execute.md` | CONFIRMED | if false: 先统一文档口径再实现

## Forks

- F1 | when: worker 仍可能看到写工具 | a) 只加强 prompt b) 过滤 worker registry + 加强 prompt | pick: b | by: V1 | reversal: reversible
- F2 | when: docs 已声明“计划任务只允许六个工具” | a) 实现运行时过滤并保留该表述 b) 退回“planner 指令”表述 | pick: a | by: V1 | reversal: reversible

## Criteria

- C1 | plan 模式 worker 的 registry 只含六个只读工具，且 `_task_system_prompt()` 明确 plan 只读、禁止 bash/write_file | task-0001
- C2 | README 与 docs/plan-execute.md 说明 worker 运行时也只读，文档契约测试通过 | task-0002

## Decisions

- D1 | 采用“过滤 worker registry + prompt 双约束”，而不是只改提示 | by: V1 | reversal: revert commit
- D2 | 不改 executor 只读闸，执行层 blocked 继续作为安全兜底 | by: V6 | reversal: revert commit

## Parked

- P1 | glob 输出中 `__pycache__` 等噪声过滤暂不做，避免扩大本包边界 | by: V2 | reversal: 后续独立包

## Binding

- branch: `spec/2026-11-10_fix-plan-worker-readonly`
