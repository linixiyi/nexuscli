# hooks runner 子进程环境清洗 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在包工作区根目录可执行。
- 清洗口径必须与 `tools/builtins.py` 的 `_child_environment` 一致：`policy.sanitize_environment` + `policy.env_sanitization` flag 分支，不另造规则。
- 不改 `sanitize_environment` 本身；不改 hook 的 stdin/stdout/timeout 语义。

## 阶段 A：runner 清洗实现

- [x] task-0001 hook 子进程环境按 flag 清洗
  - id: task-0001
  - boundary: src/nexuscli/hooks/runner.py, new:tests/test_hooks_env.py
  - origin: planned
  - expect: red
  - behavior: `fire_event` 从 `config.policy.env_sanitization` 读出布尔值下传 `run_hooks`（按 F1 只传 bool，不传整个 config）；`_run_single` 在 `create_subprocess_shell` 显式传 `env=`——flag 开启时用 `nexuscli.policy.sanitize_environment()` 的结果，关闭时 `os.environ.copy()`（与 `_child_environment` 同构）。其余（stdin payload、cwd、timeout、聚合语义）不变。
  - tests: 新增 `tests/test_hooks_env.py`，用真实 hook 命令回显子进程 env（如 `python -c "import os,json;print(json.dumps(dict(os.environ)))"` 作为 hook.command，或按现有 `tests/test_hooks.py` 的驱动方式），至少覆盖：① flag 开启时导出的 env 不含密钥形态变量名（如 `OPENAI_API_KEY`）与 `NEXUSCLI_*` 前缀键，含 PATH 等 ESSENTIAL_KEYS；② flag 关闭时密钥形态变量原样可见（历史行为）；③ `run_hooks` 签名变化不破坏既有调用方。
  - verify: `uv run --extra dev python -m pytest -q tests/test_hooks_env.py tests/test_hooks.py`

## 阶段 B：文档同步

- [x] task-0002 README 与 docs/hooks.md 同步清洗口径
  - id: task-0002
  - boundary: README.md, docs/hooks.md, new:tests/test_hooks_env_docs.py
  - depends-on: task-0001
  - origin: planned
  - expect: red
  - behavior: README:233 如实边界句改为：`bash`、后台任务派生、MCP stdio server、slash 注入**与 hooks runner** 都把清洗后的环境传给子进程（密钥形态变量剔除，`policy.env_sanitization` 可关），OS 级沙箱仍挂账；docs/hooks.md 增加「子进程环境」小节说明同一口径与 flag。措辞不得宣称 OS 级沙箱。
  - tests: 新增 `tests/test_hooks_env_docs.py`：断言 README 不再含「hooks runner 仍未清洗」、包含 hooks 子进程已清洗的表述、保留 OS 级沙箱挂账表述；docs/hooks.md 含「子进程环境」小节与 `env_sanitization` 关键词。不依赖网络与真实模型。
  - verify: `uv run --extra dev python -m pytest -q tests/test_hooks_env_docs.py tests/test_docs_contract.py tests/test_docs_integrity.py`

---

**当前进度**：由脚本计算，无需手动维护
