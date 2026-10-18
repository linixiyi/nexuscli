# NexusCLI 审计遗留收口：安全、进程、渲染、平台 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：安全与审批
- [x] A1 `-p` 接入审批回调，新增 `--yes` 与 `--no-approve` 显式语义
  - boundary: src/nexuscli/entrypoints/cli.py 的 `_run_prompt` 与参数定义；README 配置章节
  - verify: `.venv/bin/python -m pytest -q tests/test_cli_single_prompt.py tests/test_tool_approval_matrix.py`
- [x] A2 审计日志脱敏：正文截断 500 字符、密钥形态掩码、文件 0600
  - boundary: src/nexuscli/policy/audit_log.py
  - verify: `.venv/bin/python -m pytest -q tests/test_audit_redaction.py`
- [x] A3 MCP server 浏览器安全：Origin 校验、路径校验、content-type 校验、GET 405
  - boundary: src/nexuscli/mcp/server.py 的 HTTP handler
  - verify: `.venv/bin/python -m pytest -q tests/test_mcp_http_security.py`
- [x] A4 Runtime API 任务接入审批通道
  - boundary: src/nexuscli/runtime/api.py 的 ToolContext 构造
  - verify: `.venv/bin/python -m pytest -q tests/test_runtime.py`

## 阶段二：进程与超时
- [x] B1 进程组：bash 与后台任务用 `start_new_session` + `killpg`
  - boundary: src/nexuscli/tools/commands.py、src/nexuscli/tools/background.py
  - verify: `.venv/bin/python -m pytest -q tests/test_process_group.py`
- [x] B2 bash 超时真正生效（孙进程持管道时也按时返回）
  - boundary: src/nexuscli/tools/commands.py 的超时处理
  - verify: `.venv/bin/python -m pytest -q tests/test_bash_timeout.py`

## 阶段三：渲染与交互
- [x] C1 窄终端状态栏降级：优先保留权限模式名
  - boundary: src/nexuscli/entrypoints/repl.py 的 `_bottom_toolbar`
  - verify: `.venv/bin/python -m pytest -q tests/test_render.py`
- [x] C2 斜杠命令 Tab 补全
  - boundary: src/nexuscli/entrypoints/repl.py 的 completer
  - verify: `.venv/bin/python -m pytest -q tests/test_slash_completion.py`
- [x] C3 placeholder 改为真实语法 `@image:`
  - boundary: src/nexuscli/entrypoints/repl.py
  - verify: `.venv/bin/python -m pytest -q tests/test_repl.py`
- [x] C4 `/compact` 压缩预算修正
  - boundary: src/nexuscli/context/manager.py
  - verify: `.venv/bin/python -m pytest -q tests/test_context.py`
- [x] C5 `/clear` 重绘 banner 并清零上下文条
  - boundary: src/nexuscli/entrypoints/repl.py
  - verify: `.venv/bin/python -m pytest -q tests/test_repl.py`
- [x] C6 `/config` `/policy` `/audit` 长 JSON 改摘要
  - boundary: src/nexuscli/entrypoints/repl.py
  - verify: `.venv/bin/python -m pytest -q tests/test_repl.py`
- [x] C7 `/help` 加说明；`/skill <未知>` 不再静默列表
  - boundary: src/nexuscli/entrypoints/repl.py、commands/skill.py
  - verify: `.venv/bin/python -m pytest -q tests/test_skill.py`
- [x] C8 审批块紧凑化
  - boundary: src/nexuscli/entrypoints/commands/approval.py
  - verify: `.venv/bin/python -m pytest -q tests/test_approval_keys.py`
- [x] C9 非 TTY 提示不硬折行，改走 stderr
  - boundary: src/nexuscli/entrypoints/repl.py
  - verify: `.venv/bin/python -m pytest -q tests/test_repl_non_tty.py`
- [x] C10 `/effort off` 与「未设置」可区分
  - boundary: src/nexuscli/entrypoints/repl.py 的 `_effort_label`
  - verify: `.venv/bin/python -m pytest -q tests/test_effort_switch.py`

## 阶段四：平台与文档
- [x] D1 MCP client 连接复用
  - boundary: src/nexuscli/mcp/client.py
  - verify: `.venv/bin/python -m pytest -q tests/test_mcp.py`
- [x] D2 MCP 工具入参本地 schema 校验
  - boundary: src/nexuscli/mcp/client.py
  - verify: `.venv/bin/python -m pytest -q tests/test_mcp_filter.py`
- [x] D3 Runtime 并发 turn 串行化；不存在的 thread 返回 404
  - boundary: src/nexuscli/runtime/api.py
  - verify: `.venv/bin/python -m pytest -q tests/test_runtime.py`
- [x] D4 `cron next` 跳过 disabled；`cron run` 输出可直接 pipe
  - boundary: src/nexuscli/runtime/cron.py、entrypoints/cli.py
  - verify: `.venv/bin/python -m pytest -q tests/test_cron.py`
- [x] D5 `web_search` 失败给真实原因
  - boundary: src/nexuscli/web/search.py
  - verify: `.venv/bin/python -m pytest -q tests/test_web_fetch_policy.py`
- [x] D6 `eval` 在 wheel 形态下带内置场景
  - boundary: pyproject.toml 的 package-data、src/nexuscli/eval/loader.py
  - verify: `.venv/bin/python -m pytest -q tests/test_eval.py`
- [x] D7 端口占用等启动错误给可读提示
  - boundary: src/nexuscli/entrypoints/cli.py、runtime/api.py
  - verify: `.venv/bin/python -m pytest -q tests/test_cli.py`
- [x] D8 `mcp list` 展示 enabled:false；配置损坏报错
  - boundary: src/nexuscli/mcp/config.py、entrypoints/cli.py
  - verify: `.venv/bin/python -m pytest -q tests/test_mcp_filter.py`
- [x] D9 README 与实现对齐（自动快照、cron run、eval 复制树）
  - boundary: README.md、docs/eval.md、docs/cron.md
  - verify: manual: 主线程逐条对照真实 CLI 行为核对

## 阶段五：验收
- [x] E1 测试矩阵状态列同步更新
  - boundary: docs/testing-matrix.md
  - verify: manual: 主线程对照本轮修复逐条改状态标记
- [x] E2 全量质量门
  - boundary: 只跑验证，不改代码
  - verify: `.venv/bin/python -m pytest -q`

---

**当前进度**：由脚本计算，无需手动维护