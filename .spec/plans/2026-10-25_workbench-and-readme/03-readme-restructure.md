本文定稿 F1（README 重排分层）：把 743 行的 README.md 重排为「TL;DR + 特性速览表 → 快速上手 → 核心概念 → 深入专题」分层结构，收敛到 ~400 行（±10%），细节下沉 docs/。核心交付物是逐节下沉映射表与可机械校验的锚点/链接/行数验收。全部结论建立在本次实读的 README.md（743 行、33 个 H2，实测 `grep -c ""` 与 `grep -n "^## "` | wc -l）、NEXUS.md、docs/ 全部 13 个 .md、TUTORIAL.md（定位：从零跑通的运行教程，TUTORIAL.md:1-3）与 site/build.py 之上。

## 业务逻辑

### 目标结构（定稿）

新 README 的骨架（全部小节保持 H2，不引入额外 H1，三段式只体现在目录分组与节顺序上，锚点集合因此保持简单）：

```text
头部：H1 + tagline + badge（README:1-10 保留）+ 导航行 + 文档站 callout（README:12-14 保留）
TL;DR（2-3 句：是什么、怎么跑、去哪深入）                ← 吸收 README:18-21 压缩
特性速览表（领域 × 一句话 × 深入文档链接）               ← 压缩 README:63-117
目录（按 快速上手 / 核心概念 / 深入专题 三段分组）       ← 重排 README:22-55
H2｜📐 架构总览                                          ← 原样保留 README:57-61（含架构图与外链）
—— 快速上手 ——
H2｜🧭 环境要求、H2｜🚀 快速开始                         ← README:119-171 压缩保留
—— 核心概念 ——
H2｜🔧 配置（要点）、H2｜🔐 权限与会话模式（要点；Hooks 七事件名单一句置于本节内）
H2｜💬 交互命令（常用十余条速查）、H2｜🧰 内置工具（速查表；@image: 两行示例也在本节）
H2｜💾 会话与快照（要点）
H2｜💰 模型与费用（要点）、H2｜🔌 MCP（要点 + 部署边界）、H2｜📡 Runtime API
H2｜🐍 SDK
—— 深入专题 ——
H2｜📖 深入文档索引（一张表：专题 → docs 文件 → 一句话） ← 新增，20 篇全覆盖
—— 开发 ——
H2｜🧪 开发（含构建与 smoke）、H2｜📄 License            ← 保留 README:708-743
```

badge（README:7-10）、架构图与交互版外链（README:59-61）、文档站入口（README:12,14）三项按任务要求原样保留。

映射表里每一条「保留」承诺都必须在骨架上有明确的家：「保留一行」的类目以深入文档索引表的行为家，「保留要点 / 速查 / 一句」的类目以核心概念的对应 H2 为家——骨架与映射表按此逐行对照实现，不允许出现无处安放的保留项。

### 分层业务规则

1. **同一内容只在一处维护**：下沉 = 移动 + 链接，不是复制。README 保留「能力宣称句 + 如实边界要点 + 详见链接」，docs/ 承接完整口径。
2. **安全与如实边界不上沉也不消失**：MCP 部署边界警告（README:526）、`-p` 无审批按失败关闭（README:153,269）、子进程环境清洗「护栏不是沙箱」（README:106）三类边界声明，在 README 对应节至少保留一句，且整段警告块（如 README:526 的 `⚠️ 部署边界`）原样保留。
3. **docs 横幅与 README 的对应关系保持真实（章节改名与降级为索引条目都算）**：docs/ 现有 13 个 .md 中 10 个带「本文是 [README](../README.md)「X」章节的完整版。」横幅（docs/hooks.md:3、docs/permissions.md:3、docs/runtime-api.md:3、docs/subagents.md:3 等；eval.md、spec-skill.md、testing-matrix.md 无横幅、维持现状），新建 7 个文件一律沿用横幅。横幅按主题在新 README 中的形态分两个变体，重排时逐一核对更新（共 17 个：10 既有 + 7 新建）——主题仍是 README 的 H2 章节（配置、权限与会话模式、交互命令、内置工具、会话与快照、MCP、Runtime API）→ 用「「X」章节的完整版」变体；主题降级为深入文档索引条目（其余全部）→ 改用「本文是 README 深入文档索引「X」条目的完整版。」变体，避免横幅引用已不存在的章节。
4. **每个下沉点带节级锚点**：README 里的「详见」链接一律写成 `docs/<file>.md#<节标题 slug>` 形态，锚点存在性由 pytest 机械校验（见验收钩子）——「README 保留一行链接、细节在 docs/ 有着落」由此从 review 口号变成断言。
5. **NEXUS.md「信息优先级」分工不破坏**（NEXUS.md:6-13）：F1 只在 README（第 4 优先级）与 docs/ 之间移动内容，不触碰 NEXUS.md 与 AGENTS.md 正文。NEXUS.md:121-122 本就规定「实现细节、临时状态和长篇设计说明应放到对应代码、测试或 docs/」，下沉正是执行该条；NEXUS.md:95「行为或公开能力发生变化时，同步更新 README.md」在分层后的执行口径变为：先更新 docs/ 细节文档，再核对 README 对应摘要句与深入专题索引行——该口径由本文档定稿，NEXUS.md 文本无需修改（改动它超出 F1 范围）。

### 与文档站的关系（决定新文件要不要登记）

site/build.py **不自动收录** docs/*.md：`main()` 按白名单迭代 `for name in PAGER_ORDER`（site/build.py:351-355），页面图标与描述取自 `DOC_META`（site/build.py:26-41）。因此新建 docs 文件必须同步登记 `DOC_META` 与 `PAGER_ORDER` 两处，否则站点缺页且无任何报错——这条同样做成 pytest 断言（见验收钩子用例 4）。README 自身不经 build.py 渲染：`rewrite_links`（site/build.py:135-160）只把 docs 渲染页里的 README.md 相对链接改写为 GitHub blob URL（含 `#fragment`），所以 README 内锚点只需遵循 GitHub slug 规则，无需迁就站点。

## 数据模型

### 下沉映射表（核心交付物）

盘点基线：docs/ 现有 13 个 .md——cron、eval、hooks、mcp、observability、permissions、plugins、runtime-api、slash-commands、spec-skill、subagents、testing-matrix、workflows——外加 architecture.html / architecture.json / architecture.png 三个静态资产（architecture.* 不经 build.py 的 md 渲染，是直接引用的静态文件）。README 33 个 H2 逐节去向如下（行号为现状 README 行号）：

| README 现有小节（行号） | 去向 | 处理方式 |
|---|---|---|
| 头部 H1/badge/导航/站点 callout（1-16） | 保留 | 骨架不动，供 TL;DR 区复用；唯一例外：README:14 的「收录本仓库全部 13 篇深度文档」计数随新增 7 篇同步改为 20 篇 |
| 引言段（18-21） | README TL;DR | 压缩并入开头 |
| 📚 目录（22-55） | 保留 | 重排为三段式分组 |
| 📐 架构总览（57-61） | 保留 | 原样（架构图 + 交互版外链） |
| ✨ 功能特性（63-117） | README 特性速览表 | 压缩为表格；主题有 docs 归属的行加深入链接（team 协作模式等无 docs 归属的主题保留为一句话行，其细节本就只在 README 一句宣称，不存在丢失） |
| 🧭 环境要求（119-123） | 保留 | 压缩；安装细节 TUTORIAL.md:9-52 已覆盖 |
| 🚀 快速开始（125-171） | 保留 | 命令块全保；`-p` 失败关闭细则链接 docs/permissions.md#单次--p-模式下没有审批弹窗 |
| 🔧 配置（173-253） | **新建 docs/config.md** | 优先级链 / .env / provider key 表 / `--provider --model` / openai-compatible / anthropic 边界 / `!command` / `agent.max_turns` / `agent.loop_detection_threshold` 全部移入；其中 telemetry / debug_dump 完整口径与 docs/observability.md:16-53 重复，直接删 README 侧，observability 不动 |
| 🔐 权限规则（255-277） | 保留要点 | 正文与 docs/permissions.md:33-80（优先级、-p、只读直通、审批键位）重复，删；README 留 deny > ask > allow 一句 + `-p` 失败关闭一句 + 链接 |
| 🪝 Hooks 生命周期钩子（279-293） | 保留一句（置于「权限与会话模式」节内） | 正文与 docs/hooks.md:25-42（协议 + 信任机制）全量重复，删；README 在「权限与会话模式」节内留七事件名单一句 + 链接 |
| 💬 交互命令（295-374） | **新建 docs/repl-commands.md** | 完整命令表（299-354）与 `/model` 选择器（356-358）、`/usage` 双口径（362-364）、`/history`（366）、`/fork`（368）、`/goal`（370）、`/effort`（372）详解移入；`/workflow` 段（374）与 docs/workflows.md 重复直接删；README「交互命令」节（核心概念段）留常用十余条速查 + 链接 |
| 🧰 内置工具（376-408） | 保留表（「内置工具」节）+ 拆分下沉 | 工具速查表（382-392）在核心概念段「内置工具」节保留；补充说明拆三路：`read_file` pdf / grep rg 差异 / vfs 只读视图 → **新建 docs/tools.md**；`task` 后台播报（400）与邮箱（401）→ docs/subagents.md（见既有吸收表）；Shift+Tab 三模式（404-408）→ docs/permissions.md 新节 |
| 📋 Plan 模式（410-419） | docs/permissions.md 新节 | README 留一行；Shift+Tab 三态与 Plan 闸门合并为「会话权限模式」一节 |
| 🧭 Plan-and-Execute 检查点与续跑（421-436） | **新建 docs/plan-execute.md** | 图级 checkpoint、`/plan --resume`、协作式中断、无 TTL 清理边界全部移入；README 留一行 |
| 🎯 Skill 匹配与沉淀（438-455） | **新建 docs/skills.md** | 分层加载、同名覆盖、规范 frontmatter 字段、Top-K 匹配、`save_skill` 强制确认移入；README 留一行 |
| 🧠 记忆、动态 Prompt 与上下文压缩（457-482） | **新建 docs/memory-context.md** | 记忆三层、动态召回、压缩预算 80%/55%、microcompact、provider 超限重试（含「子代理路径未接」边界）移入；README 留一行 |
| 🗂 代码索引与检索（484-491） | docs/tools.md | 作为「代码索引与检索」一节移入 |
| 💰 模型、Token 与费用（493-512） | docs/config.md + docs/repl-commands.md | prices / context_window / 流式 usage 解析归 config.md；`/usage` 双口径命令面归 repl-commands.md；README 留默认 provider/model 与 `--json` 三行要点 |
| 🌐 联网工具（514-520） | docs/tools.md | 作为「联网工具」一节移入（含 SSRF 拒绝范围） |
| 🔌 MCP（522-532） | 保留要点 | 接入命令 + 部署边界警告块（526）原样保留；工具过滤 / OAuth 细节与 docs/mcp.md:68 起正文重复，删 |
| 📡 Runtime API（534-538） | 保留 | 已是最小形态；docs/runtime-api.md:3 横幅自述为该章完整版，是本次下沉的先例样板 |
| 📷 图片输入（540-555） | docs/tools.md | 作为「图片输入」一节移入；README 留 `@image:` 两行示例（置于「内置工具」节内） |
| 📸 快照（557-578） | **新建 docs/sessions.md** | auto_snapshot、`/snapshot` `/restore`、vfs 交叉引用移入；README 留一行 |
| 💾 会话与恢复（580-603） | docs/sessions.md | JSONL 布局、恢复命令面、usage 台账续算双口径、fork 语义移入；README 留一行（快速开始已有恢复命令） |
| ✅ 任务清单（605-607） | docs/tools.md | 并入，README 不再单列 |
| 🧩 子代理 task 工具（609-617） | 保留一行 | 正文与 docs/subagents.md 高度重复，删；README 留一行 + 链接 |
| 🔀 动态工作流与 /expert（619-635） | 保留一行 | 正文与 docs/workflows.md 重复，删 |
| ⏰ 定时任务 cron（637-647） | 保留一行 | 正文与 docs/cron.md 重复，删 |
| 🧪 评测回归 eval（649-662） | 保留一行 | 正文与 docs/eval.md 重复，删 |
| 🔭 可观测性与调试（664-671） | 保留一行 | 正文与 docs/observability.md 重复，删 |
| 🧱 插件系统（673-687） | 保留一行 | 正文与 docs/plugins.md 重复，删 |
| 🪄 自定义斜杠命令（689-693） | 保留一行 | 正文与 docs/slash-commands.md 重复，删 |
| 🐍 SDK（695-706） | 保留 | 入口面，示例代码块全保 |
| 🧪 开发（708-739） | 保留 | CI 一致性命令、`uv build`、smoke 全保 |
| 📄 License（741-743） | 保留 | 原样 |

### 新建 docs 文件（7 个，文件名与定位定稿）

| 文件 | 定位 | 吸收来源（README 行号） |
|---|---|---|
| docs/config.md | 配置参考：优先级链、.env 与 provider key 表、`--provider/--model`、openai-compatible 与 anthropic 边界、`!command` 密钥、`llm.prices` / `llm.context_window`、`agent.max_turns`、`agent.loop_detection_threshold`、模型与费用 | 173-253、493-512 |
| docs/repl-commands.md | 内置斜杠命令参考：完整命令表、`/model` 选择器、`/usage` 双口径、`/history`、`/fork`、`/goal`、`/effort`、`/skill load`、`/compact` 手动压缩 | 295-374（`/workflow` 段除外） |
| docs/tools.md | 内置工具与输入面补充口径：`read_file` 的 pdf、grep 的 rg 差异、vfs 只读视图边界、联网工具与 SSRF 拒绝、代码索引与检索（BM25）、任务清单、图片输入管线 | 398-402（部分）、484-491、514-520、540-555、605-607 |
| docs/skills.md | Skill 匹配与沉淀：分层加载、同名覆盖顺序、规范 frontmatter 字段（`allowed-tools` 提示性边界、`disable-model-invocation`）、Top-K 匹配、`save_skill` 强制确认 | 438-455 |
| docs/memory-context.md | 记忆、动态 Prompt 与上下文压缩：记忆三层、untrusted data 动态召回、压缩预算与 microcompact、provider 超限自动重试及边界 | 457-482 |
| docs/plan-execute.md | Plan-and-Execute 检查点与续跑：图级 checkpoint、`/plan --resume`、协作式中断、无 TTL 清理等如实边界 | 421-436 |
| docs/sessions.md | 会话、恢复与快照：JSONL 转录布局、恢复命令面、usage 台账续算双口径、fork 语义、快照与 `auto_snapshot` | 557-603 |

七个新文件的横幅变体按业务规则 3 取用：docs/config.md、docs/repl-commands.md、docs/tools.md、docs/sessions.md 的主题在核心概念段有对应 H2，用「「X」章节的完整版」变体；docs/skills.md、docs/memory-context.md、docs/plan-execute.md 降级为深入文档索引条目，用「README 深入文档索引「X」条目的完整版」变体。

### 既有 docs 文件的吸收与核对（防止「删了但 docs 没接着」）

| docs 文件 | 动作 |
|---|---|
| docs/permissions.md | **新增**「会话权限模式（Default / Auto / plan）」一节，吸收 README:404-419——现状 permissions.md:64 只有一句提及 plan 态直通，无该节。README:255-277 其余正文与 permissions.md:33-80 重复，直接删；横幅章节名随 README 章节改名「🔐 权限与会话模式」同步更新（业务规则 3） |
| docs/subagents.md | README:401「会话内 `/fork`、`/resume` 只换 writer、不重盖」一句在 subagents.md:46 **未出现**（该行只写了 run id 来源与单次回退），下沉「子代理邮箱」正文时**必须补入**；README:400 播报细节已被 subagents.md:34-37 覆盖，直接删；横幅改为「深入文档索引条目」变体（业务规则 3，子代理主题降级为索引行） |
| docs/hooks.md、docs/mcp.md、docs/workflows.md、docs/cron.md、docs/eval.md、docs/observability.md、docs/plugins.md、docs/slash-commands.md | README 对应节为重复正文，只删 README 侧，docs 正文无需补内容（hooks 协议在 hooks.md:25-30、信任机制在 hooks.md:32-42 已完整）；横幅按业务规则 3 同步改：mcp.md 对应 H2 仍在、横幅不动；hooks、workflows、cron、observability、plugins、slash-commands 6 个改为「深入文档索引条目」变体（其 README 章节降级为索引行）；eval.md 本就无横幅、维持现状。runtime-api.md 另见映射表（对应 H2 保留，横幅不动） |
| docs/spec-skill.md、docs/testing-matrix.md | 不在映射内（规范技能文档 / 测试状态表，README 无对应正文节），本轮不改 |

### 新 README 行数预算（合计落在验收带内）

| 块 | 预算（行） | 来源 |
|---|---|---|
| 头部 + TL;DR | ~34 | README:1-21 |
| 特性速览表 | ~50 | README:63-117 压缩 |
| 目录（三段式） | ~40 | README:22-55 重排 |
| 架构总览 | 5 | 原样 |
| 快速上手（环境要求 + 快速开始） | ~58 | README:119-171 压缩 |
| 核心概念 9 节（要点 + 链接；含交互命令常用速查 ~15 行与内置工具速查表 ~15 行） | ~130 | 各节压缩 + README:295-374、376-394 的保留部分 |
| SDK | ~14 | README:695-706 原样 |
| 深入文档索引 | ~32 | 新增 |
| 开发 + License | ~35 | README:708-743 原样 |
| 合计（分项求和） | ~398 | 验收带 360-440 |

若实现落带外，优先调「核心概念」要点密度，不得砍深入文档索引。

## 数据流

迁移按四步走，每步可独立验证；**提交纪律（复审修正）**：第 1、2 步必须**同一提交**落盘——用例 4 的三集合断言（`set(DOC_META) == set(PAGER_ORDER) == docs/*.md stems`）在「新 docs 文件已建而白名单未登记」的中间态必然红灯（docs 有 20 个 .md 而白名单仍 13），拆开提交会产生自带失败测试的中间态提交；第 3、4 步可各自单独提交：

1. **先建 docs/ 侧**：写 7 个新 docs 文件（内容 = README 原文移动 + 横幅 + 必要的读者视角微调），同步改 docs/permissions.md（新增会话权限模式节）与 docs/subagents.md（补 `/fork`、`/resume` 邮箱句）。此时 README 未动，站点不受影响（新文件未登记就不构建，也无报错）。
2. **登记 site/build.py**：`DOC_META`（site/build.py:26-41）与 `PAGER_ORDER`（site/build.py:43-58）各加 7 条，两处键集合必须一致（新文件在 `PAGER_ORDER` 中的顺序按内容相关性插入，实现时定）。跑站点构建 smoke（命令见验收钩子）确认 20 篇全部产出。
3. **改 README（含 TUTORIAL.md 链接修补）**：按映射表逐节删改，写入带节级锚点的「详见」链接；重排目录与特性速览表；锚点目标 = 新 docs 文件的 H2 标题。同批修补 TUTORIAL.md:282 的 4 个指向 README 节级锚点的链接（`#-权限规则`、`#-hooks-生命周期钩子`、`#-plan-模式`、`#-自定义斜杠命令`——重排后这些目标消失或改名）：权限/Hooks 类改指 `README.md#-权限与会话模式`，Plan 模式与自定义斜杠命令改指深入文档索引或直接指向对应 docs 文件，具体目标由实现者按当时的 README 结构定，由测试用例 3 机械把关。
4. **校验闭环**：`pytest tests/test_docs_integrity.py` → ruff → 站点构建 → 人工按映射表逐行 review（见验收钩子第 4 条）。

### 锚点 slug 规则（定稿）

README 与 docs 的节级锚点遵循 GitHub 规则，与 site/build.py:84-97 的 `CJKSlugify`（注释自称 GitHub-compatible）同一算法：

1. 标题转小写；
2. 删除所有非「Unicode 词字符（`\w`，含 CJK）、空白、连字符」的字符——emoji 与全半角标点在此消失；
3. 空格逐一替换为连字符（1:1，不折叠）；
4. 重复标题追加 `-1` / `-2` 去重。

现状 README 锚点全部吻合该规则，例：`## 💰 模型、Token 与费用` → `#-模型token-与费用`（README:38）、`## ⏰ 定时任务（cron）` → `#-定时任务cron`（README:48）、`## 🧭 Plan-and-Execute 检查点与续跑` → `#-plan-and-execute-检查点与续跑`（README:34）。新目录锚点与下沉链接锚点都按此规则书写，并由测试用例 1、3 复核。

### 链接在两端的生效面

- **README 端**（GitHub 渲染）：`docs/x.md#节` 链接直达 docs 文件的 GitHub 渲染锚点，slug 同上。
- **docs 端**（站点渲染）：`rewrite_links`（site/build.py:135-160）按页面类型区分——docs/*.md 渲染页（`in_docs_dir=True`）里：`README.md` 相对链接改写为 GitHub blob URL（fragment 保留）、`TUTORIAL.md` 改写为 `../tutorial.html`、其余 `.md` 改写为**同目录兄弟** `<name>.html`（`prefix = ""`，site/build.py:141-152）；`docs/<name>.html` 形态只出现在非 docs 页（tutorial/index）。新 docs 文件里的横幅链接（`../README.md`，不带 fragment）与兄弟 docs 链接（`plugins.md` 裸相对）沿用现有多数文件的写法即可，无需特殊处理。

## 接口与边界

### 校验面（本轮新增的机械接口）

新增测试文件 **tests/test_docs_integrity.py**（决定：写成 pytest 用例而非独立脚本——CI 已跑 pytest（.github/workflows/ci.yml:32-38 的 ruff 两步 + `uv run python -m pytest`），脚本不入 CI 必然腐烂；用例是纯仓库文件检查，不触碰 `Path.home()`，tests/conftest.py:19-37 的 autouse HOME/USERPROFILE 重定向照常生效，无需绕开也不受影响）。四个用例：

1. `test_readme_toc_anchors_resolve`：先剔除 fenced code block（README 有大量 ``` 块，块内行首 `#` 不是标题），提取 README 全部 ATX 标题，按上述 slug 规则生成集合（含 `-N` 去重）；提取 README 所有 `](#…)` 链接目标，断言每个目标都在集合内。
2. `test_relative_links_exist`：提取 README.md、docs/*.md、TUTORIAL.md 的行内链接目标，排除 `http(s)://`、`#`、`mailto:` 开头；相对目标按所在文件目录解析，断言文件存在（覆盖 README→docs/*.md、docs→`../README.md`、README→`docs/architecture.png`、README→LICENSE 等）。
3. `test_anchor_fragments_resolve`：对形如 `path.md#frag` 的链接，解析目标文件的标题 slug 集合，断言 frag 命中——这条把「下沉点必须真有对应小节」变成机械断言，是映射表第 4 条业务规则的执行者。
4. `test_readme_line_budget_and_site_registry`：断言 `360 <= README 行数 <= 440`（master.md 定的 ~400 ±10%）；用 `ast` 解析 site/build.py 提取 `DOC_META` 与 `PAGER_ORDER` 字面量，断言 `set(DOC_META) == set(PAGER_ORDER) == {p.stem for p in (root/"docs").glob("*.md")}`。用 `ast` 而非 import 的原因：build.py 顶部 `import markdown`（site/build.py:17），而 markdown 不是项目依赖（site/build.py:3-5 注释明确说明），import 会直接失败。

### 本轮明确不做（挂账）

- **语义级「内容零丢失」不做自动证明**：测试只保证锚点与文件存在性；「删除的每段在目标 docs 有着落」由迁移 PR 按映射表逐行人工核对——这是当前诚实的边界，不伪装成已自动化。
- **外链可达性不校验**：badge（shields.io、GitHub Actions badge）、文档站 URL、Archify 外链属网络断言，不适合进 CI。
- **TUTORIAL.md 内容与定位不改**（从零跑通教程，TUTORIAL.md:1-3；README 保留入口链接）：唯一例外是 TUTORIAL.md:282 的 4 个指向 README 节级锚点的链接——重排后这些目标消失或改名，必须随迁移同步改指新锚点（见数据流第 3 步），属链接维护、不属于内容改动；不改则测试用例 3（范围含 TUTORIAL.md）必红，与数据流第 4 步的「按报错修补」冲突。
- **不动 docs/spec-skill.md、docs/testing-matrix.md、docs/eval.md 的横幅缺失问题**：eval.md 等三个文件无「本文是 README…」横幅，属历史不一致，本轮只消费它们、不顺手改造。
- **不做旧锚点兼容重定向**：README 收敛后 GitHub 上旧锚点失效是接受的代价；docs 站不受影响（横幅链接只指 README 顶层，无 fragment）。
- **不改 NEXUS.md / AGENTS.md**：见业务逻辑第 5 条。
- **不动 site/ 的其余产物**（index.html、site.js、style.css、tutorial.html）：本轮只登记新 docs 条目。

## 验收钩子

全部命令在仓库根目录执行。

1. **锚点与链接用例**（新增 tests/test_docs_integrity.py，断言口径见接口与边界）：

   ```bash
   uv run python -m pytest tests/test_docs_integrity.py -v
   ```

   期望：4 个用例全绿。注意重排完成前用例 4 的行数断言会失败（现状 743 > 440），这是驱动信号而非缺陷；用例 1-3 在迁移过程中的任何中间态若失败，说明有死链或死锚点，按报错修补。

2. **全量回归（与 CI 完全一致的三步）**：

   ```bash
   uv run ruff check . && uv run ruff format --check . && uv run python -m pytest
   ```

   期望：全部通过。本轮不引入任何 Python 运行时改动，pytest 通过数相比基线只增不减（新增 tests/test_docs_integrity.py 的 4 个用例）。

3. **站点构建 smoke**（build.py 注释规定的唯一支持命令，site/build.py:5-7）：

   ```bash
   uv run --with markdown,pygments python site/build.py
   ```

   期望：退出码 0；stdout 逐行打印 `docs/<name>.html  <-  docs/<name>.md` 共 20 行（13 既有 + 7 新建）与 `tutorial.html <- TUTORIAL.md`；`site/docs/` 下出现 7 个新 .html。该命令会重写 site/ 下入库产物，属仓库既有先例（site/ 已提交），重排完成后连同 site/ 一起提交。

4. **人工 review 核对（不自动化，挂账见接口与边界）**：按映射表逐行确认删除正文在目标 docs 有着落；三个安全边界声明（MCP 部署边界、`-p` 失败关闭、护栏不是沙箱）在 README 目视可查；17 个 docs 横幅（10 既有 + 7 新建）按业务规则 3 的两个变体逐一核对（章节名 / 条目名与重排后 README 一致）；README:14 站点 callout 的深度文档计数已更新为 20 篇；TUTORIAL.md:282 的 4 个锚点已改指重排后真实存在的目标。
