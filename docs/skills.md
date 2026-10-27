# Skill 匹配与沉淀

> 本文是 [README](../README.md) 深入文档索引「Skill 匹配与沉淀」条目的完整版。

Skill 按 `builtin -> user -> project` 加载，同名时后层覆盖前层：

- builtin：产品默认能力
- user：`~/.nexuscli/skills/*/SKILL.md`（原生格式，跨项目复用）；另扫描 Agent Skills 规范标准目录 `~/.agents/skills/`
- project：`.nexuscli/skills/*/SKILL.md`（原生格式，最贴近当前仓库）；另扫描规范标准目录 `.agents/skills/`

每层内部扫描顺序是标准目录在前、原生目录在后，所以同层同名时**原生 `.nexuscli/skills` 目录的 Skill 胜出**（规范目录的价值在于让生态里的标准 Skill 包原样加载，不改文件即可用）。

frontmatter 在 `name` / `description` / `tags` / `version` 之外还接受两个 Agent Skills 规范字段（连字符与下划线两种写法都认）：

- `allowed-tools`：逗号分隔字符串（规范写法，如 `Bash(git:*), Read`）或内联列表。`load_skill` 把 Skill 正文注入上下文时会附一行「该技能激活期间允许的工具」提示；**如实边界：这是提示性约束，执行器不做硬限制**，真正的工具白名单仍由代理 frontmatter 的 `tools` 控制。
- `disable-model-invocation`：`true` / `1` / `yes` 时该 Skill 退出自动 Top-K 匹配候选（模型不会被提示去加载它），但 `/skill load` 命令与显式 `load_skill` 调用照常可用——挡的是自动候选，不是加载通道。

每次用户输入先用 name、description、tags 做中英文词法/字符 n-gram Top-K 匹配，再把候选交给模型决定是否调用 `load_skill`。Skill 正文只在真正加载后进入当前 ReAct 的下一模型轮；每个并发子 Agent 都有独立 Skill 缓冲区，不会串线。

当一次成功流程具备稳定输入、明确步骤和可复用边界时，模型可以调用 `save_skill` 提议写入 project 或 user 层。该工具默认拒绝覆盖已有 Skill，并强制人工确认。

## eval 驱动的提案进化

`/skill evolve <name> <rule...>` 是纯手写的规则追加；`/skill propose` 是它的评估驱动版：读取最近一次 eval 报告的失败项与成本告警，交给一次真实 LLM 调用产出结构化改进提案，再经逐条人审落地。两条路径并存，互不改动。

### 用法

```text
/skill propose <name> [--report hermetic|real]
/skill proposals <name>
```

`propose` 默认读 hermetic 轨道报告 `<cwd>/.nexuscli/evals/last-report.json`，`--report real` 改读真模型轨道的 `last-real-report.json`；`<name>` 必须是 user 层 Skill（`~/.nexuscli/skills/<name>/SKILL.md`，与 `/skill evolve` 同边界，builtin/project 不在范围）。三种空转情形都会明确提示而不是静默跳过：报告文件不存在（提示先跑 `nexuscli eval run` / `--real`）、报告里没有失败项也没有成本告警、没有可用的 LLM Key（走正常配置链报错，附 `NEXUSCLI_API_KEY` 等配置指引）。

### 提案结构与人审语义

一次 LLM 调用收到目标 Skill 全文加报告摘要（失败场景的原因、成本告警），被要求只回一个 JSON 数组，每条形如 `{"rule": "<可追加进 ## Evolved Rules 的一条规则>", "evidence": "<引用的报告场景名 + 原因/告警片段>"}`。落地前有证据门：evidence 必须对得上报告摘要——命中失败/告警场景名（大小写不敏感），或逐字引用一段足够长的失败原因——对不上的条目直接丢弃，人不会看到无据提案；LLM 回复不是合法 JSON 数组时整体报错，不会拿半成品过审。

人审逐条进行，提示带原因行（`Reason: <该条 evidence>`，与工具审批弹窗同一规范）；`y` 落地、`n` 跳过本条、`s` 跳过余下；Ctrl+C/Ctrl+D（EOF）视同 `s`，Esc 不会被拦截——按 Esc 再回车提交的是默认答案 `n`（只跳过当前条）。两条路都是失败关闭：不敲 `y` 就不会有任何条目落地。确认项经既有的 `SkillEvolutionStore.save_versioned(name, rule, reason="propose")` 落地：改动前状态冻结为 `skill_history/<name>/v<N>.md` 快照（上限 10 个），`## Evolved Rules` 追加一条带时间戳的规则，`audit.jsonl` 落一行 `op=save`——快照、回滚（`/skill rollback`）、统计（`/skill stats`）全部复用既有机制。

`/skill proposals <name>` 列出历史提案记录：时间、条数、落地数、报告轨道与文件路径。

### 非交互与失败关闭

落地门本身按 `sys.stdin.isatty()` 判定交互性，是失败关闭的：没有 TTY 可以作答时，提案照常生成并落档，但不会落地任何规则，全部条目记为 `pending`，并提示回 REPL 里审阅——没有人点头，Skill 就不变。需要如实说明的是，今天并不存在「无 TTY 跑 `/skill propose`」的生产路径：`-p` 单次模式只展开自定义斜杠命令，未匹配的 `/name` 会原样作为普通 prompt 发给模型（`entrypoints/cli.py` 的 `_expand_custom_prompt`），管道 stdin 在 REPL 入口就被拒绝（`entrypoints/repl.py`）。这个门是纵深防御，也是测试注入假答案、验证失败关闭语义的位置。

每次 propose 都会写一条记录到 `~/.nexuscli/skill_history/<name>/proposals/<ts>.json`：报告摘要（落地前的快照，供人工对照）、提案全文、每条的人审结论与落地版本号。

### 如实边界

**eval 报告是提案依据，不是落地效果验证器。** hermetic eval 运行时关闭 skill 注入（`eval/runner.py` 置 `features.skill = False` 以保证确定性），所以「改了 Skill → hermetic 分数变化」这条因果链不存在；真模型轨道是否开 skill 注入亦不在此依赖内。提案记录只保存落地前的报告摘要供人工对照；「落地后自动再跑 eval 并 diff 验证」明确挂账，本轮不做。也不要指望无监督批量改写：人审是硬门，每条落地都要过一次 y/n/s。
