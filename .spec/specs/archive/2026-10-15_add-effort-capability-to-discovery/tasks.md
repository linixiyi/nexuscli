# 模型发现透出可选推理强度 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- 新任务包使用 `YYYY-MM-DD_<verb>-<object>`，详见 `references/naming-and-commits.md`
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：澄清与初始化
- [x] 固化问题定义、关键假设与非目标
  - boundary: 只更新 `.spec/specs/2026-10-15_add-effort-capability-to-discovery/spec.md`
  - verify: spec.md 已写明网关 capabilities 实测字段、三态口径（True/False/None=未知）、非目标与 integration branch
- [x] 从主分支创建独立工作分支
  - boundary: 只执行 Git 分支检查/创建，不改业务文件
  - verify: 当前分支 `spec/2026-10-15_add-effort-capability-to-discovery` 由最新 main 创建（init 脚本输出）

## 阶段二：核心实现
- [x] 解析 capabilities 强度字段并透传 ModelListingProfile
  - id: task-parse
  - boundary: 只改 `src/nexuscli/llm/openai_compatible.py`（_ModelListing/_parse_models_payload/ModelListingProfile/_parse_capability_bool）
  - verify: `uv run pytest tests/test_model_discovery.py -q` → test_fetch_parses_effort_capability_tri_state 通过（True/False/None 三态），live 断言通过
- [x] Fetch tab 渲染强度徽标 + 激活时写入 description
  - depends-on: task-parse
  - id: task-render
  - boundary: 只改 `src/nexuscli/entrypoints/model_selector.py`（_capability_badges）与 `src/nexuscli/entrypoints/commands/model_cmds.py`（_capability_description + dataclasses.replace 写 description）
  - verify: `uv run pytest tests/test_model_discovery.py tests/test_model_selector.py -q` → 徽标渲染测试与 description 写入断言通过

## 阶段三：集成与验收
- [x] 真实网关 live 断言 + 全部回归 + CI 口径检查
  - depends-on: task-render
  - id: task-e2e
  - boundary: 只运行命令取证与测试补断言，不改业务文件
  - verify: `uv run pytest tests/test_model_discovery.py tests/test_model_selector.py tests/test_config.py -q` → 26 passed；live 断言 deepseek-v4-pro effort=True / glm-5.3-flash can_disable=True / effort=False；`uv run ruff format --check src tests` → 191 files already formatted
- [x] 完成验收检查（checklist 回填）并 done 归档提交
  - depends-on: task-e2e
  - id: task-check
  - boundary: 只更新任务包文档、检查项与验收证据
  - verify: manual: check_spec_package 门禁输出「验收结果：通过 / 已收敛」→ 成功标准 4/4、功能框全勾、任务 6/6（本项即归档动作，归档即完成）

---

**当前进度**：由脚本计算，无需手动维护
