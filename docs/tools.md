# 内置工具与输入面补充口径

> 本文是 [README](../README.md)「🧰 内置工具」章节的完整版。

NexusCLI 内置了一组 Agent 可以调用的本地工具和联网工具（速查表见 README「🧰 内置工具」节）。写文件、执行命令、远程 MCP 写操作、恢复快照等危险动作，会经过 policy、HITL 和 audit 处理。`save_skill` 也必须经过 HITL；模型可以提议沉淀，但不会静默改变后续行为。本文记录 README 速查表之外的补充说明与如实边界（均以实现为准）。

## read_file 的 PDF 支持

`read_file` 对 `.pdf` 后缀走 pypdf 整篇文本提取（不做分页范围），再套用与文本文件相同的行窗口。pypdf 是可选依赖，缺失时返回带安装指引的错误：`uv sync --extra pdf`（或 `pip install "nexuscli[pdf]"`）。

## grep 的 rg 加速与语义差异

`grep` 对目录搜索先探测 PATH 上的 rg：命中则由 rg 子进程加速并映射回同一 `路径:行号: 内容` 格式，rg 缺失或失败时回退纯 Python 扫描。已知语义差异：rg 会跳过二进制文件，纯 Python 路径以 `errors="ignore"` 读取一切，文本文件结果一致。

## task 后台委派与收尾播报

`task` 的 `run_in_background: true` 立即返回后台任务 id 与落盘报告路径（`~/.nexuscli/bg-subagents/<task_id>.md`），不阻塞当前回合；回合结束时 REPL 会把**已**完成的后台子代理一次性播报给用户（`[bg-subagent] completed/failed ...`，不进入模型上下文）。后台子代理是进程内状态：未完成任务随 REPL 退出而终止，已完成的报告文件保留在磁盘上；`task_stop` 可终止仍在跑的后台子代理（状态记为 `stopped`，不再回播），对已到终态的任务是 no-op。子代理工具（含 `mailbox_post` / `mailbox_read` 邮箱）的完整口径见 [subagents.md](subagents.md)。

## vfs_ls / vfs_read 只读快照视图

`vfs_ls` / `vfs_read` 把历史快照挂载为只读虚拟文件系统（逻辑路径 `/` 即快照根），snapshot 引用接受与 `/restore` 相同的 id 或 1-based 序号。**只读是结构保证**：视图类上不存在任何写/改/删方法，路径解析强制 resolve 后留在快照根内（`..` 链与盘符/UNC 真实绝对路径一律拒绝）。`vfs_read` 与工作区 `read_file` 语义一致（整文件读取 + 行窗口，无快照专属大小上限）——快照创建时已剔除 `build`/`dist`/`node_modules` 等目录。如实边界：这两个工具只注册进主 agent 的工具表；子代理（自建 registry）、SDK 与 `mcp serve` 路径不经由它们暴露——全路径接入挂账。可写视图、多快照 diff 与审计层均未做。快照的创建与恢复见 [sessions.md](sessions.md)。

## 代码索引与检索

`/index [path]` 扫描工作区中的文本文件，把逐行内容写入 `.nexuscli/code_index.sqlite3` 的 `code_chunks` 表建立本地代码索引，默认跳过 `.git`、`.venv`、`node_modules` 等目录。`/search <query>` 与内置 `search_code` 工具基于同一索引做关键词检索：多个关键词按 AND 匹配，命中行按 **BM25 相关性排序**（纯 Python 实现，k1=1.5、b=0.75，无第三方依赖；词频按大小写不敏感子串计数，CJK 关键词同口径工作），按分降序返回 Top-N（N 即 `limit` 参数，默认 20，同分按路径与行号稳定并列）。`search_code` 工具输出末尾附一行命中统计（命中行数、候选窗口、检索词）；代码变动后重新运行 `/index` 即可刷新。如实边界：这是词法检索不是 embedding 语义检索——索引不做向量化、不联网；BM25 的语料统计在候选窗口（首词 LIKE 预筛的 500 行）内计算，窗口外的行不参与排序。

```text
/index .
/search 权限评估
```

## 联网工具

`web_search` 使用 DuckDuckGo HTML 搜索，返回标题、URL 和摘要。

`web_fetch` 可以抓取公开 HTTP/HTTPS 页面，并做基础正文提取。它会拒绝 `file://`、loopback、私有网络和内网地址，降低 SSRF 风险。

如果需要登录态、浏览器状态或 JS 渲染页面，建议使用 Chrome DevTools MCP。

## 图片输入

NexusCLI 支持在 prompt 里引用图片：

```text
分析这张截图 @image:./screenshots/page.png
```

也支持绝对路径和远程图片：

```text
解释这张图 @image:/Users/me/Desktop/diagram.png
看看这个图片 @image:https://example.com/image.png
```

本地图片会自动压缩、缩放，并在需要时把透明底铺成白底，再转为 data URL。如果当前 provider/model 不支持多模态输入，NexusCLI 会自动降级为文本元信息，不会把不支持的图片 payload 发给模型。

## 任务清单

Agent 处理多步任务时可以通过内置工具 `todo_write` 维护一份任务清单：每次提交完整列表，标记 `pending / in_progress / completed` 与优先级。清单保存在项目 `.nexuscli/todo.json`，可用 `todo_read` 读取，工具返回值即格式化后的清单状态，跨会话仍然有效。
