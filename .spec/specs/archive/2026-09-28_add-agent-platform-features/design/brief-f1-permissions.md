# F1 权限规则引擎 — 收尾简报（第一波）

## 现状
F1 主体**已实现**（工作区未提交）：`config.py` 的 `PermissionsConfig` + `_merge_permission_lists`；`policy/permission_rules.py`（parse_rule/subject_for/rule_matches/evaluate_permissions，deny > ask > allow）；`tools/executor.py` 已接入（deny 短路返回、allow 跳过 HITL 但 `hitl_mode=="always"` 仍提示、never 态 ask 兜底 deny）；`tests/test_permissions.py` 已有 242 行用例。

**唯一失败**：`tests/test_permissions.py::test_permission_lists_concat_across_user_and_project_configs`。

## 根因（已定位，直接修）
测试只 `monkeypatch.setenv("HOME", ...)`，但 win32 上 `Path.home()` 优先读 `USERPROFILE`（已实验证实：HOME 与 USERPROFILE 同时设置时 `Path.home()` 返回 USERPROFILE 的值），用户层配置根本没被读到。**修法**：该测试同时 `monkeypatch.setenv("USERPROFILE", str(home))`（不改 `config.py` 的 `_home()`——在 Windows 上改成优先 HOME 会把 Git Bash 的 `/c/...` 风格路径带进生产配置解析，风险更大）。顺带检查 `tests/test_permissions.py` 里其他用 `HOME` 的测试是否有同样问题（当前它们恰好通过就不动断言，只防回归）。

## 补齐清单（对照 tasks.md verify 逐条自查，缺则补）
- deny 拒绝且结果含规则原文；allow 跳过 HITL（auto 态）；ask 强制走 approval_callback；
- `bash(git diff:*)` 前缀匹配、`bash(npm run *)` fnmatch、`write_file(src/**)` 路径匹配、`web_fetch(domain:...)` 域名匹配（含子域）各至少 1 用例；
- user+project 两层列表拼接（修好的那条）；无任何规则时行为与旧版一致（回归用例：auto 态 requires_approval 工具仍提示、never 态直接放行）；
- 审计记录规则命中：deny 命中时 audit 行 outcome=deny、approver="permission-rule"（读 `policy/audit_log.py` 的 `AuditLog.record` 签名，用 tmp_path 传 `policy.audit_log_path` 断言 JSONL 内容）。

## 红线
- 只改：`tests/test_permissions.py`（必要时）、`src/nexuscli/policy/permission_rules.py`、`src/nexuscli/config.py`、`src/nexuscli/policy/__init__.py`、`src/nexuscli/tools/executor.py`。改动尽量小，不重构已有实现。
- 不改 HITL 三模式（never/auto/always）既有语义；不动 `.spec/`；不 git commit。
- 自验命令（只跑本文件，勿跑全量）：`uv run python -m pytest tests/test_permissions.py -q` 全绿后，再跑 `uv run python -m ruff check src/nexuscli/policy/ src/nexuscli/config.py src/nexuscli/tools/executor.py tests/test_permissions.py`。
