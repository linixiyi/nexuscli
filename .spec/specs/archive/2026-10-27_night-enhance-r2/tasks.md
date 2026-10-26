# 夜间③功能升级第二轮 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] task_0001 审计值级掩码（P2-8）
  - boundary: 只改 src/nexuscli/policy/audit_log.py（新增两条值级正则并接入 _mask_tokens）与 tests/test_audit_redaction.py 新增用例
  - verify: `uv run --extra dev python -m pytest tests/test_audit_redaction.py -q; printf '\nAUDIT_EXIT=%s\n' "$?"`（成功判定：Bearer/JWT 掩码用例过，既有脱敏用例不回归）
- [x] task_0002 Runtime 任务重试上限（P2-19）
  - boundary: 只改 src/nexuscli/runtime/tasks.py 的 requeue_expired 与 tests/test_runtime.py 新增用例
  - verify: `uv run --extra dev python -m pytest tests/test_runtime.py -q; printf '\nRUNTIME_EXIT=%s\n' "$?"`（成功判定：毒任务转 failed 用例过，租约恢复既有用例不回归）
- [x] task_0003 usage.db 保留窗口（P2-16）
  - boundary: 只改 src/nexuscli/llm/usage_store.py（prune 方法 + stats 内 best-effort 调用）与 tests/test_usage_history.py 新增用例
  - verify: `uv run --extra dev python -m pytest tests/test_usage_history.py tests/test_session_usage.py -q; printf '\nUSAGE_EXIT=%s\n' "$?"`（成功判定：旧行清理用例过，统计既有用例不回归）
- [x] task_0004 /turn-resume 入清单（P2-6）
  - boundary: 只改 src/nexuscli/entrypoints/repl.py 的 SLASH_COMMANDS 列表与 docs/repl-commands.md 总表
  - verify: `uv run --extra dev python -m pytest tests/test_slash_commands.py tests/test_workflow.py tests/test_init_command.py tests/test_integration_platform.py -q; printf '\nSLASH_EXIT=%s\n' "$?"`（成功判定：清单成员断言全绿）
- [x] task_0005 全量回归
  - depends-on: task_0001, task_0002, task_0003, task_0004
  - boundary: 只读执行全量 pytest、ruff、uv build；失败回写本包本轮修复
  - verify: `uv run --extra dev python -m pytest -q; printf '\nPYTEST_EXIT=%s\n' "$?"; uv run --extra dev ruff check .; printf 'RUFF_EXIT=%s\n' "$?"; uv run --extra dev ruff format --check .; printf 'FMT_EXIT=%s\n' "$?"; uv build; printf 'BUILD_EXIT=%s\n' "$?"`（成功判定：四个 EXIT 真值齐全且为 0）

---

**当前进度**：由脚本计算，无需手动维护
