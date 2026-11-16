# glob 工具噪声目录过滤 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在包工作区根目录可执行。
- 过滤集合必须复用 `file_ops.SKIP_DIRS`（F1），不得新建第二份清单。
- 不改 glob 工具 schema（不加 exclude 参数，F2）；不改 `directory_tree`/`grep` 的现有排除行为。

## 阶段 A：glob 过滤实现

- [x] task-0001 glob_files 按 SKIP_DIRS 过滤命中
  - id: task-0001
  - boundary: src/nexuscli/tools/file_ops.py, new:tests/test_glob_noise_filter.py
  - origin: planned
  - expect: red
  - behavior: `glob_files` 在把命中转相对路径时，丢弃任一路径段命中 `SKIP_DIRS` 的条目（与行 55 既有排除逻辑同构：`any(part in SKIP_DIRS for part in mp.relative_to(root).parts)`）；limit 截断只对过滤后的条目计数；排序、`(no matches)` 空结果、越界 pattern 拒绝语义不变。
  - tests: 新增 `tests/test_glob_noise_filter.py`（tmp_path 构造 `src/a.py`、`__pycache__/b.pyc`、`node_modules/x.js`、`.venv/c.py` 等结构），至少覆盖：① `**/*.py` 不含 `__pycache__`/`.venv` 命中但含 `src/a.py`；② limit=1 时截断发生在过滤后（噪声不消耗配额）；③ 目录本身被命中时（如 `*` pattern）噪声目录不出现；④ 排序仍为字典序。
  - verify: `uv run --extra dev python -m pytest -q tests/test_glob_noise_filter.py`

## 阶段 B：文档同步

- [x] task-0002 docs/tools.md 补 glob 排除口径
  - id: task-0002
  - boundary: docs/tools.md, new:tests/test_glob_noise_docs.py
  - depends-on: task-0001
  - origin: planned
  - expect: red
  - behavior: 在 docs/tools.md 检索工具相关段落补一句：`glob` 默认排除 `SKIP_DIRS` 噪声目录（与 `directory_tree` 默认排除同集），limit 只对有效命中计数；不新开小节时挂在现有检索段落内。措辞不宣称支持自定义排除。
  - tests: 新增 `tests/test_glob_noise_docs.py`：断言 docs/tools.md 同时包含 glob 与 SKIP_DIRS 七个目录名中的代表项（至少 `__pycache__`、`node_modules`）、「与 directory_tree 同集/同口径」表述；不依赖网络。
  - verify: `uv run --extra dev python -m pytest -q tests/test_glob_noise_docs.py tests/test_docs_contract.py tests/test_docs_integrity.py`

---

**当前进度**：由脚本计算，无需手动维护
