# 2026-11-16_fix-hooks-env-sanitization - Completion Summary

## Delivery
- task-0001 hook 子进程环境按 flag 清洗
- task-0002 README 与 docs/hooks.md 同步清洗口径

## Evidence
- Final-tree verifies passed; see runs.jsonl.

## Decisions
- D1 |  `run_hooks`/`_run_single` 的 `sanitize_env` 为 keyword-only 参数且默认 `True`（与 `policy.env_sanitization` 默认一致；直接调用 `run_hooks` 的既有调用方走安全默认，3 参位置调用签名不变） | by: V1 | reversal: reversible
- D2 |  flag 关闭口径的测试断言排除 shell 自管理变量（`PWD`/`OLDPWD`/`SHLVL`/`_`/`PS1`）后逐键一致——`/bin/sh` 包装进程 spawn 时必然改写这几个，与 runner 传参语义无关，其余键值必须与父环境全量一致 | by: V1 | reversal: reversible

## Parked

## Emergent tasks

## Lessons
- See .spec/lessons.md for retained project lessons.
- Archived: 2026-11-16T15:59:35+00:00

## Git
- Commit interval: 0e95a1fdcc8407b30e39e55fba65f79ef3b3a1d2
