# 会话、恢复与快照

> 本文是 [README](../README.md)「💾 会话与快照」章节的完整版。

## 快照

快照是**按需**的：`features.auto_snapshot` 默认关闭，因为每回合两次全量复制在工作区大时能吃掉十几秒。打开后每次 Agent run 会尽力创建：

- `pre-turn`
- `post-turn`

```json
{ "features": { "auto_snapshot": true } }
```

快照保存在 `~/.nexuscli/snapshots/`，不会写入项目 `.git`。无论开关如何，手动的 `/snapshot` 与 `/restore` 始终可用。

REPL 中可以使用：

```text
/snapshot
/restore 1
/snapshot clean
```

Agent 侧也可以经 `vfs_ls` / `vfs_read` 把快照挂载为只读视图做改前对比，无需整体 `restore`（见 [tools.md](tools.md)）。

## 会话持久化

REPL 的每轮对话会自动追加到 `~/.nexuscli/sessions/<id>.jsonl`（首行为会话元信息，之后每行一条消息）。只打开不对话不会产生会话文件；`/clear` 会开启一个新会话。

恢复方式：

```bash
uv run nexuscli sessions            # 列出当前项目的会话（--all 查看全部项目）
uv run nexuscli -c                  # 继续本工程最近一次会话
uv run nexuscli --resume <id>       # 按会话 id（或 id 前缀）恢复
```

会话内切换：

```text
/resume                             # 列出最近会话
/resume 2                           # 按序号切换，后续对话继续追加到该会话
/resume <id-前缀>
/fork [title]                       # 分叉当前会话为新会话并切换过去（原会话不变）
/tree [id]                          # 输出当前会话的父子/分支树（可传 id 锚定其他会话）
```

## 会话树

会话元信息行（JSONL 首行）带有可选的 `parent_id` / `branch` / `created_at` 字段：

- `created_at`：会话创建时刻（Unix 秒）。
- `parent_id`：本会话分叉自哪个父会话；`""` 或缺省表示根会话。旧会话没有该字段时照常可读，默认视为根；旧版只写了 `forked_from` 的分叉会话在树里仍回退挂到其来源会话之下。
- `branch`：fork 时记录的分支名。`/fork` 缺省按父会话下已有的 `fork-N` 自动编号（`fork-1`、`fork-2`…），也可以显式指定。

`/tree` 以当前会话为锚点：向上补全父链到根，向下展开所有分支子树，当前节点带 `(current)` 标记，兄弟分支用 `├─` / `└─` 连接：

```text
* 20261108-101010-a1b2c3  fix the login bug
  ├─ 20261108-101512-d4e5f6  (branch: fork-1)  my fork
  └─ 20261108-103045-b7c8d9  (branch: fork-2)  another angle  (current)
```

树只展开与锚点会话连通的部分，无关会话不出现；未落盘的会话（只打开没对话）显示为带提示的单独根节点。

单次模式也支持恢复：`uv run nexuscli -p "继续刚才的任务" -c` 会把历史会话注入 react 模式（plan/team 单次模式不支持注入历史）。

## 恢复的 usage 续算

每个完成的普通回合都会把一条 `type: usage` 记录追加进会话 JSONL（model/provider、input/output/total/cache hit/miss、成本估算），恢复时把这些记录求和为历史基线，`/usage` 的 `historical_total` 即「基线 + 本进程累计」，两者之和等于该转录的 usage 台账。`/clear` 与 `/fork` 切换到新转录时会同步清零基线与本进程累计（fork 的转录只拷贝消息、不拷贝 usage 行，所以基线从零开始）；历史收缩重写转录时 usage 行会保留，不丢账。单次 `-p` 模式不写转录，其 `--json` 的 `session_usage` 是「恢复基线 + 本次运行」的提示性口径，不会持久化。
