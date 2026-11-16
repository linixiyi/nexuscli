<!-- spec-format: 3 -->

# hooks runner 子进程环境清洗 - 项目范围

## Goal

- goal: hooks 子进程与 `bash` 工具同口径的环境清洗——`_run_single` 派发的 hook 命令在 `policy.env_sanitization` 开启（默认）时拿到经 `sanitize_environment` 清洗的环境，密钥形态变量与 `NEXUSCLI_*` 前缀不再继承；flag 关闭时保持全量 `os.environ` 的历史行为；README 如实边界句同步。
- literal ask: 「hooks 环境清洗 (推荐)」——用户 2026-11-16 会话批准的改进包（README:233 挂账「hooks runner 仍未清洗」）。
- stated purpose: 补齐 README 已如实披露的环境清洗缺口：任意 hook 命令（用户或项目层配置）当前继承宿主全量环境，密钥形态变量（API Key、Token）会泄漏给 hook 子进程。
- done means: hook 子进程 env 在 flag 开启时不包含密钥形态变量与 `NEXUSCLI_*`，包含 ESSENTIAL_KEYS；flag 关闭时与 `os.environ` 全量一致；README 的「hooks runner 仍未清洗」表述更新为已清洗口径；两种 flag 状态均有单测覆盖。

## Charter

- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md

## Scope

- in: src/nexuscli/hooks/runner.py, tests/test_hooks.py, new:tests/test_hooks_env.py, README.md, docs/hooks.md, new:tests/test_hooks_env_docs.py
- out: src/nexuscli/tools/**, src/nexuscli/policy/**, src/nexuscli/entrypoints/**, pyproject.toml, uv.lock, frontend/**

## Assumptions

- A1 | `fire_event` 持有 `config` 但 `run_hooks`/`_run_single` 不持有，环境注入点在 `_run_single` 的 `create_subprocess_shell` | fact | probe: `sed -n '61,115p' src/nexuscli/hooks/runner.py` | CONFIRMED | if false: 重新定位 config 到子进程 env 的传递链
- A2 | `sanitize_environment(base, overrides)` 位于 `nexuscli.policy.sandbox`，剔除密钥形态与 `NEXUSCLI_*` 前缀并强保留 ESSENTIAL_KEYS；`tools/builtins.py` 的 `_child_environment` 是参照实现 | fact | probe: `sed -n '85,105p' src/nexuscli/policy/sandbox.py` + `sed -n '668,690p' src/nexuscli/tools/builtins.py` | CONFIRMED | if false: 改用实际清洗入口并保持同口径
- A3 | `policy.env_sanitization` 默认 True，bash/后台任务/slash 注入已按该 flag 分支 | fact | probe: `grep -n "env_sanitization" src/nexuscli/config.py src/nexuscli/tools/builtins.py` | CONFIRMED | if false: 对齐实际 flag 名称与默认值
- A4 | README:233 现有句为「hooks runner 仍未清洗，OS 级沙箱挂账」，无测试断言该旧短语 | fact | probe: `grep -rn "hooks runner 仍未清洗" README.md docs tests` | CONFIRMED | if false: 先对齐文档契约测试再改实现
- A5 | 既有 `tests/test_hooks.py` 通过 `fire_event`/`run_hooks` 间接驱动 `_run_single`，不 monkeypatch env | fact | probe: `grep -n "create_subprocess\|os.environ" tests/test_hooks.py` | CONFIRMED | if false: 新测试沿用现有驱动方式

## Forks

- F1 | when: config 需要从 `fire_event` 传到 `_run_single` | a) 整个 config 下传 b) 只传 `sanitize_env: bool` | pick: b | by: V1 | reversal: reversible
- F2 | when: flag 关闭时的行为 | a) 保持全量 `os.environ`（与 bash 工具历史行为一致） b) 无条件清洗 | pick: a | by: V6 | reversal: reversible

## Criteria

- C1 | flag 开启时 hook 子进程 env 不含密钥形态变量与 `NEXUSCLI_*`、含 ESSENTIAL_KEYS；flag 关闭时与 `os.environ` 全量一致 | task-0001
- C2 | README 如实边界句改为 hooks 子进程已同口径清洗（OS 级沙箱仍挂账），docs/hooks.md 补环境清洗小节，文档契约测试通过 | task-0002

## Decisions

## Parked

## Binding
- branch: `spec/2026-11-16_fix-hooks-env-sanitization`
- frozen: 2026-11-16T15:50:05+00:00
