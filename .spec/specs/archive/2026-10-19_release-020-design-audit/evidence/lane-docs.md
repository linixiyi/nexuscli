# Lane A 发布文档与蒸馏 — 证据(2026-10-19)

边界:PRODUCT.md、README.md、AGENTS.md(新)、CHANGELOG.md(新)。其他文件零修改。

## 做了什么

### 1. PRODUCT.md 刷新

实测命令与输出:

```
$ ls tests/test_*.py | wc -l
     101
$ .venv/bin/python -m pytest --collect-only -q        # pytest 9.1.1 / Python 3.13.13,exit=0
$ awk -F': ' '/: /{s+=$2; n++} END{print "files="n, "total="s}' /tmp/collect.txt
files=101 total=1048
```

(pytest 9.1.1 的 `-q --collect-only` 输出为每文件一行 `file: count`,无汇总行;总数为逐行求和。)

改动:
- Positioning:`82 个 tests/test_*.py / 951 个测试函数` → `101 个 / 1048 个`。
- 删除 Evidence on Hand 失效行「工作区存在未提交的 TUI 观感改动…」——依据:git status
  仅剩 `.spec/specs/2026-10-19_release-020-design-audit/` 未跟踪,TUI 改动已随历史提交入库。
- CONTEXT_STALE 类字段:`grep CONTEXT_STALE PRODUCT.md` 无标记(rc=1);对可数事实逐项实测:
  - 31 个 slash 命令 ✓(从 `repl.py` 的 `SLASH_COMMANDS` 字面量提取计数 = 31,未改)
  - 25 个内置工具 ✓(`nexuscli.tools.builtins.get_builtin_tools()` 实测 25);主 agent
    路径并入 2 个 vfs 视图 ✓(`src/nexuscli/bootstrap.py:23-24`)
  - CI 矩阵 Linux 3.11+3.12 / Windows 3.12 ✓(`.github/workflows/ci.yml:16-19`)
  - builtin_skills 1 个(web-access)✓
  - docs/*.md 实测 **13** 篇(原记 12;testing-matrix.md 为新增)→ 更新两处:
    Operating Context「12 篇文档」→「13 篇」、Evidence on Hand「12 篇主题文档」→「13 篇」;
    `site/docs/` 实测 13 页,站点与 docs 同步
  - README 体积 55K → 58K(实测 59,517 bytes)→ 更新 Evidence on Hand
- 其余历史结论文本(init 三轮结论、pi 式方向、单色约束等)未重写。

### 2. README.md

```
$ grep -nE '951|948|955' README.md          → 无匹配
$ grep -nE '[^0-9]82[^0-9]|^82[^0-9]|[^0-9]82$' README.md → 无匹配
$ grep -nE '889|948|950|951|955|1046|1048' README.md → 无匹配(rc=1)
```

README 从未写测试计数,零改动、结构未动。任务按字面执行(grep 全部旧数字),结论是"无可改"。

### 3. AGENTS.md(新建)

通读全部 6 份归档包 `completion-summary.md` 的「AGENTS.md 增量提议」:
- 有提议块的只有两份:**2026-10-18_fix-audit-residual**(6 条)、
  **2026-10-18_fix-real-usage-gaps**(5 条);
- 其余四包(effort-capability、provider-model-discovery、ux-firstimpression、
  verify-zero-and-yellow)该节均为「无」。

合成一页 12 条守则,分五节(测试与隔离 / 进程与信号 / 审批与写域 / MCP / 渲染与模型网关),
每条注明来源包,两包重复条目(rich.Markdown 硬换行、MCP initialize)合并共引。
本包五事实写入前先在代码核实锚点:

| 事实 | 代码锚点(已读) |
| --- | --- |
| 能力驱动 vision | `src/nexuscli/llm/model_profiles.py:24-26`(三态 `vision`,`None` 回落名字启发式) |
| 审批原因行 | `src/nexuscli/entrypoints/commands/approval.py:59`(`Reason: {description}`) |
| 写域守卫 allow 语义 | `src/nexuscli/tools/builtins.py:627-660`(/dev/null 剔除;allow 不重复问;hitl_mode=always 例外;approve_run 单次) |
| 严格交替网关摘要角色 | `src/nexuscli/context/manager.py`(摘要走 user 角色;GLM 400 code 11133 注释及真实网关验证记录) |
| pty 测试 HOME 隔离约定 | `tests/conftest.py:19-36`(HOME 与 USERPROFILE 双重定向 autouse fixture) |

未复读 README/NEXUS.md:先读 NEXUS.md 全文避重(其「信息优先级」第 2 条本就预留
`AGENTS.md`(若后续新增),本次落地使该引用成立)。

### 4. CHANGELOG.md(新建 0.2.0 草稿)

内容来源(均为实读,非凭印象):
- `git show 5d0c261`(= 上一包 6786e34 系在集成分支上的同内容提交):矩阵 62 项收口
  (17 ⬜ 双补 + 44/45 🟡 真实运行)、两轮工作流修复 13 缺陷的完整清单、测试 948→1046;
- `git log -1 201aece`:MCP 会话复用回归测试(三次调用同 pid、计数 1→2→3;缺参本地拒发);
- `git log -1 edaf815 / 3a9ad4c`:核对上下文边界(只取其矩阵文档演进脉络,未将其 27+11
  项收口计入 0.2.0 节——按任务定义 0.2.0 内容限于 6786e34 系 + 本包);
- 本包 spec.md 功能范围(Lane B/C/D/E/F)作为「本轮」条目,草稿头注明以合入后实际状态为准;
- Keep a Changelog 分类(Added/Fixed/Changed),中文,日期 2026-10-19。

## 契约变更清单

- 本 lane 只写 4 个文档文件,**未改任何渲染字符串与测试断言 → 契约变更:无**。
- 实测确认无测试断言引用被改数字:`grep -rn '951|1048' tests/*.py` 无匹配。
- NEXUS.md 对 AGENTS.md 的预留引用因新建而成立,无需改 NEXUS.md(亦不在边界)。

## 未跑的检查

- 全量 pytest + ruff:按包纪律由工作流尾段统一跑,本 lane 未跑。
- Lane B/C/D/E 的 verify(各自新测试/站点构建):非本 lane 边界,未跑;
  CHANGELOG 中对应条目为发布范围描述,最终状态以 F1 全量门禁为准。

## 遗留与移交(详见返回结果 feedback)

1. `spec.md` §5 写「changelog 用 scripts/generate_changelog.py」,但 `scripts/` 目录不存在
   (`ls scripts/` 为空)——F1 需创建该脚本或直接采用本手写草稿,请主线程裁决。
2. PRODUCT.md「Evidence on Hand」中 rich_renderer「无 Table」表述与现状有出入
   (`repl.py:465/500/578` 仍有无框化未完成的 Table;`/config /policy` 已 `box=None`)。
   属 Lane D 裁决范围,为避免并发写冲突本 lane 未动,Lane D 落地后需同步该行。
3. PRODUCT.md/CHANGELOG 中的测试计数(101/1048)是 2026-10-19 实测点;Lane B/D 还在新增
   测试文件,F1 发版前应以全量收集数复核(CHANGELOG 草稿内已注明)。
