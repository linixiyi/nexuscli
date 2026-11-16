<!-- spec-format: 3 -->

# glob 工具噪声目录过滤 - 项目范围

## Goal

- goal: `glob_files` 的命中默认过滤 `SKIP_DIRS` 噪声目录（`.git`/`.venv`/`node_modules`/`dist`/`build`/`target`/`__pycache__`），噪声不再占满 100 条 limit、不再污染 plan worker 等只读消费者的输出；limit 只对过滤后的有效命中计数。
- literal ask: 「glob 噪声过滤 (推荐)」——用户 2026-11-16 会话批准；源自 2026-11-10_fix-plan-worker-readonly 的 P1 parked 项「glob 输出中 `__pycache__` 等噪声过滤」。
- stated purpose: 当前 `glob_files` 用 `glob_module.glob` 原样收集命中，`**/*.py` 这类常见 pattern 会先被 `__pycache__`/`.venv` 命中占满 limit，真实源码文件反而不可见。
- done means: `glob` 工具输出不含任何路径段命中 `SKIP_DIRS` 的条目；limit 截断发生在过滤之后；非噪声命中行为与排序不变；有针对性单测覆盖（含 limit 截断顺序）。

## Charter

- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md

## Scope

- in: src/nexuscli/tools/file_ops.py, new:tests/test_glob_noise_filter.py, docs/tools.md, new:tests/test_glob_noise_docs.py
- out: src/nexuscli/tools/builtins.py, src/nexuscli/tools/registry.py, README.md, src/nexuscli/agent/**, pyproject.toml

## Assumptions

- A1 | `glob_files` 现状是 `glob_module.glob` 原样收集，无任何噪声过滤，limit 在收集循环内截断 | fact | probe: `sed -n '309,331p' src/nexuscli/tools/file_ops.py` | CONFIRMED | if false: 以实际收集循环为准设计过滤点
- A2 | `SKIP_DIRS` 常量已存在且被同文件其他函数（如行 55 的路径排除、`directory_tree` 默认排除）复用 | fact | probe: `sed -n '41,56p' src/nexuscli/tools/file_ops.py` + `sed -n '354,354p' src/nexuscli/tools/file_ops.py` | CONFIRMED | if false: 新建同义常量并保持单点定义
- A3 | glob 工具 schema 无 exclude 参数，`_glob_files` handler 只透传 pattern/limit；加参数会动公共工具 schema | fact | probe: `grep -n -A8 '"glob"' src/nexuscli/tools/builtins.py | head -20` | CONFIRMED | if false: 重估是否顺手暴露 exclude 参数（仍默认不动 schema）
- A4 | glob 行为无既有直接单测（无 test 文件断言 glob_files 输出内容） | fact | probe: `grep -rn "glob_files" tests/*.py` | CONFIRMED | if false: 新测试与既有断言对齐，不重复造 fixture
- A5 | docs/tools.md 现无 glob 专节，README 工具表只列名不述行为 | fact | probe: `grep -n "glob" docs/tools.md README.md` | CONFIRMED | if false: 按现有文档结构插入说明而非新开小节

## Forks

- F1 | when: 过滤集合的选择 | a) 复用 `SKIP_DIRS` 单一常量 b) glob 专用新集合（含 `.mypy_cache` 等） | pick: a | by: V1 | reversal: reversible
- F2 | when: 是否给 glob 工具加 exclude 参数 | a) 不加，纯行为修复 b) 加可选参数 | pick: a | by: V6 | reversal: reversible

## Criteria

- C1 | `glob_files` 输出不含路径段命中 `SKIP_DIRS` 的条目，limit 截断在过滤后生效，排序与错误语义不变 | task-0001
- C2 | docs/tools.md 说明 glob 默认排除噪声目录（与 `directory_tree` 同集），文档契约测试通过 | task-0002

## Decisions

- D1 | 过滤判断作用在 `relative_to(root)` 之后的相对路径段上（非绝对路径段），避免 workspace 根自身位于名为 `build`/`target` 等的父目录时命中被全量误杀；与行为字段「同构」表述一致 | by: V1 | reversal: reversible
- D2 | docs/tools.md 以独立小节「glob 的噪声目录过滤」呈现（镜像该文档既有 per-tool 小节结构，如 grep/read_file 小节），并如实补「不支持自定义排除清单」边界；测试对「不支持自定义排除」的否定表述做了防误判处理（先剔除否定短语再断言） | by: V1 | reversal: reversible
- D3 | task-0002 的 red 前置以改动前 `grep -i glob docs/tools.md` 零命中证实（原文件无任何 glob/directory_tree 提及），未单独跑一次注定失败的 pytest | by: V1 | reversal: reversible

## Parked

## Binding
- branch: `spec/2026-11-16_fix-glob-noise-filter`
- frozen: 2026-11-16T15:50:10+00:00
