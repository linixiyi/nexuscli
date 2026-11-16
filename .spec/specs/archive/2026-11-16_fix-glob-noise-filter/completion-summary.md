# 2026-11-16_fix-glob-noise-filter - Completion Summary

## Delivery
- task-0001 glob_files 按 SKIP_DIRS 过滤命中
- task-0002 docs/tools.md 补 glob 排除口径

## Evidence
- Final-tree verifies passed; see runs.jsonl.

## Decisions
- D1 |  过滤判断作用在 `relative_to(root)` 之后的相对路径段上（非绝对路径段），避免 workspace 根自身位于名为 `build`/`target` 等的父目录时命中被全量误杀；与行为字段「同构」表述一致 | by: V1 | reversal: reversible
- D2 |  docs/tools.md 以独立小节「glob 的噪声目录过滤」呈现（镜像该文档既有 per-tool 小节结构，如 grep/read_file 小节），并如实补「不支持自定义排除清单」边界；测试对「不支持自定义排除」的否定表述做了防误判处理（先剔除否定短语再断言） | by: V1 | reversal: reversible
- D3 |  task-0002 的 red 前置以改动前 `grep -i glob docs/tools.md` 零命中证实（原文件无任何 glob/directory_tree 提及），未单独跑一次注定失败的 pytest | by: V1 | reversal: reversible

## Parked

## Emergent tasks

## Lessons
- No retained lessons.
- Archived: 2026-11-16T15:56:37+00:00

## Git
- Commit interval: 677fcf5d39f64d45d662b567bd7d672c53a59c3c
