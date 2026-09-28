# Spec 任务包工作流（spec 技能详解）

spec 是一个规范驱动开发（spec-driven development）技能：把一次多步开发变成一个有边界、有验收、有留痕的「任务包」。本文介绍它的核心概念、命令体系、质量门禁与多代理编排模型，所有行为描述均以技能源码为准。

## 📚 目录

- [概述](#-概述)
- [核心概念：任务包（Development Record）](#-核心概念任务包development-record)
- [命令全景](#-命令全景)
- [执行模型与断点续跑](#-执行模型与断点续跑)
- [质量门禁体系](#-质量门禁体系)
- [分支与提交规范](#-分支与提交规范)
- [多代理编排](#-多代理编排)
- [扩展槽机制](#-扩展槽机制)
- [知识沉淀与结构治理](#-知识沉淀与结构治理)
- [在 nexusCLI / ZCode 中的实践](#-在-nexuscli--zcode-中的实践)

## 🧭 概述

spec 把「开发一个功能」变成「交付一个任务包」：范围、任务、验收标准在开工前写清楚，执行过程逐项验证留痕，收尾时归档、蒸馏、提交，全程可复查。

**单编排者**。主会话始终是编排者：路由决策、验收判定、`done`/`push` 两个门禁的授权永不移出主会话。子代理只承担有界委派切片（探察、规划、评审、干净所有权的实现片段），关键路径——规划、集成、独立验证、验收、共享胶水文件——留在主线程。

**磁盘真源**。`.spec/` 目录下的文件是唯一事实来源。会话中断、换会话、隔天续跑，状态都在磁盘上：勾过的任务保持完成，下一个就绪任务就是继续点。`run` 的续跑指令本身就是从包状态恢复，不依赖会话记忆。

**门禁文化**。`check` 脚本与 Git hooks 是硬边界：包校验脚本以退出码说话，pre-commit/pre-push 钩子跑全量校验，`--no-verify` 绕过钩子会被宿主侧门禁直接拦截，把无关改动混进包提交同样会被挡下。口头的「通过了」不算数，真实运行过的验证才算数。

**适用与不适用**。适用：多步、跨会话、需要验收留痕的功能开发、重构、安全审查。不适用：单行修复、问答、临时实验——这些直接做就好，不必套流程。

## 📦 核心概念：任务包（Development Record）

一个任务包对应一次完整的开发循环，落在 `.spec/specs/YYYY-MM-DD_slug/`，由三份文档组成：

```text
.spec/
├── specs/
│   ├── 2026-06-12_add-push-stage/     # 活动包：三件套
│   │   ├── spec.md                    # 边界与验收：范围、假设、技术决策、集成 branch
│   │   ├── tasks.md                   # 任务清单：每项带 boundary 与 verify
│   │   └── checklist.md               # 勾选状态：验收项与证据
│   └── archive/
│       └── 2026-06-12_add-push-stage/ # 归档包：四件套（三件套 + completion-summary.md）
├── docs/                              # 项目级知识沉淀（YYYY-MM-DD_slug_topic.md）
├── artifacts/
└── architecture/                      # 模块图：module-index.md / module-dag.md / module-dag.mmd
```

`tasks.md` 里的每个任务都带契约字段，缺一不可：

| 字段 | 作用 |
| --- | --- |
| `id` | 可选的稳定标识（小写 `task-` 前缀字符串），任务重排后身份不漂移；依赖声明同时接受位置 ID 与显式 ID |
| `boundary` | 该任务允许改什么、不允许改什么；越界改动就是违规 |
| `verify` | 验收方式，必须是真实可运行的命令/测试/构建；勾选前必须真的跑过 |
| `depends-on` | 依赖任务的 id（逗号分隔），声明执行顺序；不声明则视为相互独立 |

完成后 `done` 阶段归档：三件套整体移入 `archive/`，并补上 `completion-summary.md` 凑成四件套——归档必须是原子操作（隔离 → 复验快照 → 写摘要 → 切换），且只在 `check_spec_package.py` 退出码为 0 时允许。

两个不属于单个包的目录：`.spec/docs/` 存放项目级知识沉淀（可复用的工程事实，绝不镜像到全局载体）；`.spec/architecture/` 存放当前模块图（module-index 与 module DAG），属于整个项目，由各任务包引用。

## ⌨️ 命令全景

| 命令 | 说明 |
| --- | --- |
| `/spec` | 默认入口：内部先跑 `route_spec_package.py` 判定包状态，自动接续最合适的阶段（无包则 `new`，可续跑则 `run`，要求评审则 `check`，以此类推） |
| `/spec:new` | 创建任务包：澄清目标、锁定范围/任务/验收，Git 仓库内完成分支预检并绑定集成分支 |
| `/spec:run` | 执行全部任务：按依赖顺序逐项完成、真实验证、勾选；中断后从磁盘包状态续跑 |
| `/spec:check` | 人机评审轮：跑门禁脚本、补齐证据，未通过项写回包内并当场修复 |
| `/spec:done` | 归档收尾：生成 completion-summary、蒸馏知识到 `.spec/docs/`、创建包提交 |
| `/spec:push` | `done` 之后：合并主分支、推送、删除已合并工作分支（带归档门禁与 SHA 租约） |
| `/spec:update` | 中途增删改任务与范围（checkpoint 生命周期保护，已交付任务不可失效） |
| `/spec:status` | 跨包总览：各包进度、阻塞项、建议下一步 |
| `/spec:goal` | 一句话目标走全链路：自规划 → 建包 → 执行 → 验证 → 归档 → 提交 → push |
| `/spec:doctor` | 环境自检与安全修复（Python/git、技能安装、命令文件、骨架、hook 指针） |
| `/spec:organize` | 结构治理：基于机器事实做第一性原理审计，永不删除，retired 归档 |

**触发方式**分两类宿主：

- Claude Code 用斜杠命令 `/spec:<stage>`，如 `/spec:new`、`/spec:check`。
- Codex 与通用 skill-aware CLI 用 `$spec` 触发，如「$spec 继续跑这个包」，再用 `spec:new` / `spec:run` / `spec:check` / `spec:done` / `spec:push` / `spec:update` / `spec:status` / `spec:doctor` / `spec:organize`。技能源 `references/commands.md` 列举的宿主为：Codex、Gemini CLI、Grok Build、OpenCode、OpenClaw、Hermes、Pi 等。

命令示例中的 `scripts/` 相对技能根目录；对其他项目操作时传 `--root <project>`，这是脚本的工作目录约定。

## 🔁 执行模型与断点续跑

`run` 阶段是一个单会话执行循环：

1. **读包**：读三份文档，确认唯一集成分支（受保护、游离、不匹配、双重绑定的分支一律失败关闭）。
2. **选就绪任务**：从依赖满足且未完成的最早任务开始，一次一个。
3. **执行**：真实修复、实现、测试发生在主线程、有合同的 sidecar lane 或受管槽位协议内。
4. **verify**：跑该任务 `verify` 行描述的验证；没跑过验证的勾选不算完成，不存在「应该可以」。
5. **勾选并循环**：通过才勾选，继续下一个就绪任务，直到全部勾完，进入 `check` 门禁。

**中断恢复**：会话被打断后，重跑 `run` 即从 `tasks.md` 的磁盘状态继续——已勾任务保持完成，下一个未勾的就绪任务就是接续点。用户的暂停/中止和待回复的消息永远优先于继续执行循环。

**阻塞记录**：无法完成的任务在包内打 `!` 标记或写 `blocked:` 状态行，并告知用户具体影响与已完成内容；绝不静默跳过，更不伪造完成。

**check→fix 循环**靠两个停止条件收束：一是语义收敛——某轮修复后不再出现上一轮集合之外的新增未通过项（「无新增项」）；二是预算上限——包内声明的「最多 N 轮」。同时止损归因：同一门禁连续 N 轮失败就停止重跑（原样重跑不是修复），把失败归因到具体任务/边界——怀疑验收标准过宽就拆成更细的可验证项，或修 `verify` 本身，或标注转人工；两者都够不着时才升级给人。门禁全绿后循环正常结束，进入 `done`。

## 🛡 质量门禁体系

`check_spec_package.py`（单包）与 `check_all_spec_packages.py`（提交/推送前全量）是包状态的唯一真源，结论以脚本输出为准，口头「passed」不作数。检查项就是脚本当前输出的清单——不要手抄门禁列表。

**证据锚点**。在 Git 项目里填写 `## 验收证据` 时捕获一个顶层锚点：`- 证据锚点：HEAD <sha> @ <ISO-8601 时间>`（取目标项目当前 HEAD）。锚点一旦写入即生效：校验器对比当前 HEAD，HEAD 移动后门禁报「证据过期需重跑取证」，必须重新取证并更新锚点。被截断、分页或超时的命令输出必须标记 `truncated`，收窄范围重取，绝不作为通过依据。至少要有一条非占位的脚本/测试/构建输出证据。

**五种问题处置枚举**（归档关闭时使用，只允许这五个值）：`resolved_current`（本轮已完成）、`resolved_followup`（指向已归档的后续包）、`accepted_risk`（接受风险）、`external_blocked`（外部阻塞）、`non_actionable`（不可行动）。注意区分：check 轮内的处置标签 `fix_this_round` / `followup` / `accepted_risk` 只是当轮动作标注，与归档枚举是两套词表，不得混用；严禁把未通过项回写成成功结果。

**可选门禁节**采用「出现即生效」语义：`checklist.md` 里写了 `## 边界回归`（越界负样本）、`## 跨载体一致性`、`## 项目结构与文档可信度` 中的任何一节，该节就强制执行——每个勾选框都必须勾上才能通过，本轮不适用的行也要带 `N/A：<理由>` 内联注记后勾选；没写的节不参与判定也不产生失败。初始化模板默认自带 `## 边界回归`。

**Git 仓库钩子与宿主侧守卫共四件**（`pre-commit`/`pre-push` 由 `install_git_hooks.py` 渲染安装进 Git 仓库；Stop 守卫与磁盘真源门禁是宿主侧事件钩子，按宿主 hooks 配置注册，不属于 Git hooks）：

- `pre-commit` / `pre-push`：调用 `check_all_spec_packages.py` 做全量校验，推送钩子还按推送范围校验归档门禁与 SHA 租约。
- Stop 收敛守卫（`claude_stop_guard.py`）：主会话想停下时检查包是否收敛，未收敛则拦下并给出缺口清单；绑定歧义失败关闭；钩子永不扩大 Git 授权。
- 磁盘真源门禁（`spec_disk_truth_gate.py`）：工具层 PreToolUse 拦截直接的 `git commit`/`git push`——`--no-verify` / `-n` 跳钩子直接拒绝；禁止 Git alias 与 `--git-dir`/`-c` 重定向仓库；禁止 `commit -a/--all/--include` 或 pathspec 混提（强制先单独 `git add` 再纯 `git commit`，确保 index 真源被校验）；校验不过时返回「Spec 磁盘真源门禁未通过，禁止提交、推送或宣称完成」。

## 🌿 分支与提交规范

**分支**：

- 本技能创建的分支一律用 `spec/` 前缀，默认名 `spec/YYYY-MM-DD_<slug>`；一个活动包绑定且仅绑定一条集成分支，初始化时拒绝绑定已被其他活动包占用的分支，两条活动包永不共享分支。
- 保护分支清单：`main`、`master`、`develop`、`release/*`、`hotfix/*`，以及远程默认分支——拒绝在其上创建或执行包工作。
- `new` 的分支预检：要求干净工作树（脏树先分流，绝不把外部脏状态带进新包）；先切回 `main` 并以 `--ff-only` 快进更新；再从更新后的 main 创建集成分支。远程因传输层故障（连接拒绝、超时、502 等）不可达时自动降级：改从本地 main 建分支并在输出与 `spec.md` 记录本地模式，网络恢复后可重试；而认证失败、权限拒绝、仓库缺失、main 不可快进仍然中止，绝不带病继续。

**提交**：

- Conventional Commits 格式 `<type>(<scope>): <description>`，type 取 `feat | fix | docs | style | refactor | perf | test | build | ci | chore | revert`；footer 用 `Spec: <slug>` 锚定任务包（旧 `[Spec-#N]` 编号已废弃）。
- slug 动词受控表（与 Conventional Commit 类型同一心智模型）：

  | slug 动词 | 含义 | 对应 commit 类型 |
  | --- | --- | --- |
  | `add` | 新能力或新模块 | `feat` |
  | `fix` | 缺陷修复 | `fix` |
  | `refactor` | 重构，行为不变 | `refactor` |
  | `update` | 文档、配置、依赖或版本 | `docs` / `chore` |
  | `remove` | 移除 | `chore` / `revert` |
  | `docs` | 纯文档 | `docs` |
  | `test` | 测试基建 | `test` |
  | `chore` | 工具链或杂项 | `chore` |

- **评审三角**：slug 是身份锚点（`new` 时定死）、commit 是动作日志（footer 指回 slug）、completion-summary 是结果综合（`done` 时生成并引用 slug 与 commit）。一个 slug 对应 1..N 个提交，包内各提交类型可以与 slug 动词不同（`add-billing` 包里可以有 `feat`/`test`/`docs`）；`done` 的归档提交必须带 `Spec: <slug>` footer 以保可追溯。检索路径：人看 `git log --oneline` → 打开提交 → 顺 footer 找到包 → 读 summary；LLM 直接 `grep "Spec: <slug>"`。
- 提交纪律：只有 `done` 归档并创建包提交，只有 `push` 合并/推送/删分支；绝不把改文件和 commit 塞进同一条 bash 调用，绝不 `--no-verify`。

**changelog**：`generate_changelog.py` 是按需的手动发布助手（不是 `done`/`push` 的自动步骤），把 Git 历史转成规范中文条目，格式 `- 【类型】作用域：描述 (spec_slug)`：

```bash
./scripts/generate_changelog.py --from v1.0.0 --to HEAD              # 指定区间
./scripts/generate_changelog.py --spec-only --from v1.0.0            # 只取 spec/* 分支提交
./scripts/generate_changelog.py --from main --to HEAD --output CHANGELOG.md --prepend
```

commit 类型到 changelog 类型的映射：`feat`→【新增】、`fix`→【修复】、`docs`→【文档】、`style`→【样式】、`refactor`→【重构】、`perf`→【性能】、`test`→【测试】、`build`→【构建】、`ci`→【CI】、`chore`→【杂项】、`revert`→【回退】；不符合 Conventional Commits 的简单格式提交统一记【变更】。

## 🤖 多代理编排

**定位声明**：spec 有子代理能力，但定位是「主会话唯一编排 + 有界 sidecar 委派」，不是自主多代理系统。路由、验收、`done`/`push` 门禁授权始终留在主会话；一个会话同一时间只拥有一个包、一条集成分支。

### 五路由词表

路由判定是五选一的单 token：`local`（小而耦合，主线程直接做）、`explore`（理解缺失，2-3 条定向探察）、`build`（ownership 可按模块/文件干净切分的实现）、`review`（代码已存在，风险优先，并行评审 lane）、`external`（确需隔离 git 状态/长时运行/多终端时的升级项，本轮无外部后端）。

机器判定入口：

```bash
python3 scripts/route_decision.py --text "<goal>"
```

以下为本机实跑输出。跨子系统任务：

```json
{"route": "review", "score": 2, "reason": "scores={'local': 0, 'explore': 0, 'build': 1, 'review': 2, 'external': 0}; route=review", "lanes": ["前端", "后端 / 数据", "认证 / 安全"], "risks": [], "channel_profile": {"shared_pool": false, "note": "no single-channel signal; default parallel dispatch"}, "override": false}
```

小修改：

```json
{"route": "local", "score": 2, "reason": "scores={'local': 2, 'explore': 0, 'build': 0, 'review': 0, 'external': 0}; route=local", "lanes": [], "risks": [], "channel_profile": {"shared_pool": false, "note": "no single-channel signal; default parallel dispatch"}, "override": false}
```

盘点调研类：

```json
{"route": "explore", "score": 5, "reason": "scores={'local': 0, 'explore': 5, 'build': 1, 'review': 0, 'external': 0}; route=explore", "lanes": [], "risks": [], "channel_profile": {"shared_pool": false, "note": "no single-channel signal; default parallel dispatch"}, "override": false}
```

输出字段（以 `scripts/route_decision.py` 为准）：`route`（五路由之一）、`score`（该路由的启发式命中分）、`reason`（分值表与判定依据）、`lanes`（lane 草案：业务面 lane 仅 explore/build/review 生成；`risks` 非空时任何路由都会追加独立「安全评审」lane，业务面 lane 不能替代它）、`risks`（命中的信任边界/破坏性信号）、`channel_profile`（advisory 共享池提示 `{shared_pool, note}`，校验脚本绝不拿它做门禁）、`override`（是否被显式覆盖）。`--route <token>` 可显式覆盖启发式（非法 token 退出码 2，覆盖结果按最终路由重算 lanes/score/reason，载荷永不自相矛盾）；`--root <project> --slug <slug>` 让判定吃包内上下文——包标题、未完成任务标题及其 boundary/verify 行并入文本，包内合法的 `### 5.4 编排策略` route 作为显式覆盖（非法或缺席则交给 run 门禁拦截）。

lane 是草案：只有 route ∈ explore/build/review 且 ownership 切片干净时，主会话才依据 assignment contract spawn 有界进程内 sidecar，随后在主线程合并结果并亲自跑最终验证。存疑时取 `local`。

### 三层触发叠加

1. **编排路由**：上面的五路由判定决定拓扑（主线程 / sidecar lanes / 升级 external）。
2. **书面 assignment contract 五字段**：`goal`（确切问题或切片）、`scope`（允许动的文件/模块）、`excluded areas`（禁触区域，含凭证卫生——真实凭证值不进合同文本、lane 提示、命令行参数、验收证据与蒸馏笔记，经 env 文件/宿主侧注入，示例用 `<admin-token>` 占位）、`output`（简明发现或补丁摘要）、`verification`（跑过什么、什么未验证）。写不清就不并行。
3. **槽位触发独立于路由**：任务形状命中槽位触发器时，执行段移交受管协议。两个内置槽位各有判定脚本——

```bash
# team-loop（循环收敛），本机实跑：
python3 slots/team-loop/scripts/loop_route.py --text "让多个代理循环迭代收敛直到评审通过，每轮 batch 跑完再评审"
```

```text
[loop-route] 推荐 mode=until-converged score=6（循环/收敛语义 x3）
{"mode": "until-converged", "maxRounds": 8, "concurrency": 3, "retry": {"maxAttempts": 3, "baseSec": 5.0, "maxSec": 60.0, "jitter": 0.3}, "stalenessSec": 300.0, "timeoutSec": 7200.0, "budgetTasks": 64}
```

```bash
# workflow-runner（确定性扇出），本机实跑：
python3 slots/workflow-runner/scripts/workflow_route.py --text "从多个视角并行评审这份设计再修复问题" --json
```

```json
{
  "workflowRecommended": true,
  "mode": "parallel-review",
  "score": 2,
  "reason": "多维并行评审 x1",
  "suggestedConfig": {
    "mode": "parallel-review",
    "pattern": "dimensions -> findings -> adversarial verify -> synthesize（review-changes 模式）",
    "defaultEffort": "low",
    "verifyEffort": "high",
    "agentBudgetGuideline": "单项任务 <10 agents；确需更大规模由用户显式提升"
  },
  "suggestedSurface": [
    {"surface": "workflow-tool", "host": "claude-code", "how": "宿主 Workflow 工具：inline JS 脚本（agent/pipeline/parallel + schema 结构化返回）"},
    {"surface": "subprocess-fanout", "host": "pi", "how": "slots/workflow-runner/scripts/workflow_fanout.py --backend pi"},
    {"surface": "subprocess-fanout", "host": "codex", "how": "slots/workflow-runner/scripts/workflow_fanout.py --backend codex（可达性由硬超时兜底）"}
  ]
}
```

`loopRecommended=true` 与 `route=local` 共存是合法语义：槽位激活与编排路由相互独立，命中即把执行段移交受管协议。

### sidecar 能做什么

定向探察（2-3 条 explorer）、规划、代码/安全评审、e2e 验证、有干净 ownership 的有界实现切片。循环收敛、批量扇出、评审-修复闭环走 team-loop 的磁盘真源协议（init → round → task → admit → spawn → heartbeat → result → backoff → terminate → converge）；确定性扇出走 workflow-runner（代码决定扇出数、验证门与收敛环，而非模型即兴）。

### sidecar 不能做什么

- 路由决策、验收、`done`/`push` 门禁授权永不移出主会话；共享胶水文件留主线程；lane 写域不得重叠（除非显式定义落地顺序）。
- 自报结果永不完成任务：任务 `verify` 必须真实运行后才能勾选。
- `external` 路由无后端时降级 in-process 或串行交接，绝不中途发明控制面；绕过受管路径的外包一律禁止。
- 没有 `max_parallel` 之类的并发钳制（lane 数由工作形状决定）；但共享池通道（单网关/单模型池）触发配套协议：切片最小化、lane 死亡三振（打捞 → 至多一次缩小切片重派 → 第二次仍死则由主线程吸收该切片）、突发传输故障后退避片刻再恢复并行。

### `### 5.4 编排策略` 节

`spec.md` 的 `## 5. 技术决策` 下可选小节。声明该节即作为本包的编排契约：`route` 必须是五词表中的单 token（反引号可选，`build（主线程）+ review` 这类多 token 值会被拒），ownership / waiting strategy / verification gate 作为同级 bullet 写在 route token 之外；该节缺席不阻塞包（默认 local）。

### agents/ 目录

`agents/` 不是可执行代理，是两个只读 sidecar 合同模板：`orchestrator.md`（路由拓扑建议）与 `planner.md`（范围/阶段规划），frontmatter 声明 `tools: ["Read", "Grep", "Glob"]` 白名单，硬规则为永不编辑文件、永不宣称完成。Claude Code installer 把安装者副本发布到 `~/.claude/agents/` 成为原生命名代理（带 `.spec-skill-install` marker 保护，用户同名文件未经 `FORCE=1` 拒绝覆盖）；ZCode 等宿主没有同名原生代理时，读合同原文作为 prompt 注入可用的通用代理，并在交接中注明替换。

## 🔌 扩展槽机制

槽位是 spec 的可插拔能力机制，三层合同：`slots/<name>/` 内的 manifest 声明 → `slot_registry.py` 校验注册（`python3 scripts/slot_registry.py list|validate`）→ `install_slot_hooks.py` 安装/卸载钩子（`--slot <name> [--remove]`）。激活规则：任务形状命中槽位触发器时，执行段交给该槽位 README 的协议；与编排路由相互独立。

### team-loop 槽

基于 agents-team 工具面（`spawn_agent` / `wait` / `close_agent`）的循环编排管理，解决自动触发、通用循环与原语缺失三个缺口：

- 三模式状态机：`until-converged`（循环到收敛）/ `fixed-rounds`（固定轮数）/ `batch-fanout`（批量扇出），run/round/task 三层磁盘状态。
- 原语齐备：指数退避加抖动的重试、并发准入（`admit=false` 必须等待再 spawn）、心跳与 staleness 看门狗（默认 300 秒）、协作中断恢复（interrupt/外部 STOP 哨兵/resume 校验后续跑）、可组合终止条件。
- 三个 hook：`UserPromptSubmit` 注入路由建议（不阻塞）、`Stop` 收敛守卫（未收敛的活跃 run 拦停并给下一步）、`TeammateIdle`/`TaskCompleted` 质量门（in_flight 任务没有记录 ok 就不放行）。
- 磁盘真源在项目 `.agents/runtime/loop/<run-id>/`（run.json/events.jsonl 同步落盘，会话记忆不算数）；一次一个 run，多个 run 并行时守卫失败关闭。
- 宿主适配表（agents-team 工具面）：Pi 走 pi-intercom 扩展的 `intercom` 工具；Claude Code 走原生 agent teams 加 `SendMessage`（需 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`）；Codex 走 `codex agents`/`codex exec`，通信即磁盘（决策问答写进 run.json/events.jsonl）；其他宿主按自带消息面映射，缺失的能力如实声明、永不编造通信语义。

### workflow-runner 槽

把执行段委派给确定性编排面（fan-out 数量、验证门、收敛环都是代码）：

- 四模式：`batch-fanout` / `parallel-review` / `perspective-panel` / `review-fix-loop`。各模式验收点（合并回主线程前必须可指认）：batch-fanout——每个 item 落 ok/error 终态并留产物路径，无静默缺项；parallel-review——每个维度的 findings 经对抗验证存活或带理由剔除；perspective-panel——judge 评分留痕，胜者综合引用次优想法；review-fix-loop——以 dry pass 收敛（整轮无新发现），末轮结果留痕。
- Claude Code 需要 Workflow 工具在工具列表中 **且** 用户显式 opt-in（槽位可以检测和建议，但绝不能代用户 opt-in）；任一前置不满足就降级为普通 sidecar 编排，主链不受影响。
- pi/codex 用 `workflow_fanout.py` 子进程扇出（有界并发、逐项硬超时）+ JSONL 磁盘真源作证据；后端不在 PATH 时整批 fail-fast，绝不把未验证的运行当证据。

**两槽触发消歧**：形状同时命中（批量扇出、评审-修复闭环在两槽触发器里都有）时按宿主分流——Claude Code 且 Workflow 工具可用、用户已 opt-in → workflow-runner（确定性编排优先）；否则 → team-loop（agents-team 工具面）。

## 🧹 知识沉淀与结构治理

**蒸馏只进项目 `.spec/docs/`**：`done`/`goal` 沉淀的知识只落在执行项目自己的 `.spec/` 树内，绝不写入全局载体（`~/.claude/CLAUDE.md`、用户级 `AGENTS.md` 或任何用户级记忆文件）。只蒸馏项目特定的工程事实——技术难点与解法、关键架构决策与取舍、典型坑与规避、可复用的操作模式；spec 流程机制属于技能本身，永不作为蒸馏内容。

**凭证卫生**：真实凭证值不进任务包文档、验证命令行、验收证据与蒸馏文本；按名称和注入路径（env 文件/宿主侧注入）引用，示例一律用 `<admin-token>` 这类占位符。

**`organize` 第一性原理审计**：先跑 `organize_project_structure.py` 拿机器事实（顶层清单、存活 vs 历史引用图、废弃候选、发现类、架构一致性），再默认在目标项目建新包执行修复。铁律：永不删除；retired 文件/目录/代码/过时文档归档到 `<specs-dir>/archive/retired/YYYY-MM-DD/<原相对路径>` 并附 `MANIFEST.md`；移动需要三个证明（不是声明的入口、零存活引用或文档与机器事实矛盾、已被后继资产取代），缺一就保留并记录。判定四值：keep / archive / merge / migrate；一个确认的发现类要等到每个成员都有处置才算完成。`--check` 是硬门禁：module-index 声明的文件必须存在、module-dag 与索引模块集一致，结构可信当且仅当退出码为 0。

**`doctor` 环境自检**：检查 Python ≥ 3.9、git、技能布局与脚本可编译性、安装 marker 与漂移、十宿主目录扫描、Claude 命令文件、ZCode symlink、项目 Git 状态、`.spec/specs/` 骨架、hook 指针是否漂移；`--fix` 只修安全项（仅 installer 拥有的路径，删除前先备份到 `~/.spec-skill-backups/`）；结论以脚本输出为准，退出码 0/1/2。

## 🧩 在 nexusCLI / ZCode 中的实践

**触发与安装**。本机（ZCode 宿主）以 user 层技能安装在 `~/.zcode/skills/spec/`，`doctor` 的 `zcode.symlink` 检查项守护该链接不悬空。`$spec` 是 Codex 与通用 skill-aware CLI 的触发约定（宿主清单见「命令全景」一章）；nexusCLI 自身的技能加载层为 builtin → user（`~/.nexuscli/skills/`）→ project（`.nexuscli/skills/`），把 spec 技能放入用户或项目技能目录后，输入中的 spec 相关意图经词法匹配命中，由模型决定调用 `load_skill` 加载技能正文。

**与 nexusCLI 子代理的映射**。spec 的 sidecar 委派合同可直接映射到 nexusCLI 的 `task` 子代理机制：

| spec 概念 | nexusCLI 对应 |
| --- | --- |
| 只读探察 lane（explore 路由） | 内置 `explore` 子代理：只读工具白名单，绝不修改内容 |
| 有界实现切片 / build-review 类委派 | 内置 `general-purpose` 子代理：一次完成委派任务并回报 |
| assignment contract 五字段 | `task` 工具的 `description`（goal 概述）+ `prompt`（scope/excluded/output/verification 写全的自包含委派包） |
| 「委派不递归、有界」 | 子代理深度限制为 1，且 `task` 工具从子代理工具集中移除 |
| 「验收/门禁留主会话」 | 权限规则与 HITL 审批回调对子代理原样透传；plan 态下 `task` 与其他非只读工具一样被硬拒 |

互补关系正在这里：nexusCLI 保证子代理的每次工具调用仍过权限/审批/审计链，spec 保证子代理的自报结果永不替代主会话的真实 verify——两层约束叠加，委派才有边界。

**端到端走查**（从 `new` 到 `push`）：

1. 用一句话提出目标，技能创建任务包 `2026-09-28_add-payment-export/`，分支预检通过后从最新 main 建 `spec/2026-09-28_add-payment-export` 集成分支。
2. `spec.md` 写清边界（只动导出模块）与验收（导出命令真实跑通 + 单测通过）；`tasks.md` 拆 5 个任务，每个带 `boundary`/`verify`，集成任务声明 `depends-on`。
3. `run`：路由判定取 `local`（单模块、无并行收益），按依赖顺序逐任务执行，每项跑过 `verify` 再勾选；中途第 3 个任务发现上游接口字段缺失，打 `!` 标记记录原因并告知影响，换下一个就绪任务。
4. `check`：跑 `check_spec_package.py`，门禁报「导出 CSV 缺少编码声明」未通过——写回未通过项（证据缺口 + 处置枚举 `fix_this_round`），当场修复，补齐证据锚点后重跑门禁转绿。
5. 填完 `checklist.md`（含 `## 边界回归` 负样本节，本轮不适用的行记 `N/A` 后勾选），门禁全绿。
6. `done`：归档四件套，蒸馏一条「导出编码坑」到 `.spec/docs/`，以 `feat(export): add payment export with encoding declaration` + `Spec: 2026-09-28_add-payment-export` footer 创建包提交。
7. `push`：归档门禁通过、SHA 租约校验后合并 main、推送、删除已合并工作分支；远程不可达时降级本地合并并提示网络恢复后重跑。
8. 事后任何会话都能从归档与 Git 记录复查这轮开发：包 → summary → commit → diff 全程可追溯。
