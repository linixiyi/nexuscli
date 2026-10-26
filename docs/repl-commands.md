# 内置斜杠命令参考

> 本文是 [README](../README.md)「💬 交互命令」章节的完整版。

进入 `uv run nexuscli` 后，可以使用这些 slash commands：

```text
/help
/exit
/clear
/resume
/resume <index-or-id>
/history <query> [--all]
/context
/compact [focus]
/init [notes]
/memory
/memory search <query>
/memory stats
/memory delete <id>
/memory clear
/save <fact>
/config
/tools
/hitl default|auto
/policy
/audit [N]
/index [path]
/search <query>
/plan <task>
/plan --resume <plan_id>
/team <task>
/team --plan <task>
/model
/model <model-id>
/model <provider> <model-id>
/usage
/usage stats [N天]
/fork [title]
/goal set <text>
/goal show
/goal clear
/effort off|minimal|low|medium|high|xhigh|max
/skill
/skill list
/skill show <name>
/skill load <name>
/skill on <name>
/skill off <name>
/skill reload
/mcp
/task
/task add [--mode react|plan|team] <task>
/task cancel <task_id>
/task log <task_id>
/workflow run <file>.json
/expert <topic>
/snapshot
/snapshot save [label]
/snapshot clean
/restore <snapshot-id-or-index>
/turn-resume
/turn-resume <checkpoint-id>
```

## /model 交互式模型选择器

`/model` 会打开交互式模型选择器：`Tab` 或左右方向键在 `Default`、`Custom`、`API 拉取` 之间切换，上下方向键选择模型，`Enter` 立即切换当前 Agent。`Custom` 中可以选择已保存的 BYOK 模型、创建新的 DeepSeek/GLM/OpenAI-compatible 配置，或按 `d` 删除配置。自定义配置保存在权限为 `0600` 的 `~/.nexuscli/models.json`；建议填写 API Key 环境变量名，只有显式输入 API Key 时才会把密钥写入该文件。

`API 拉取` tab 面向 OpenAI Chat Completions 协议的自建网关/中转站（例如 `http://localhost:20128/v1`）：首次进入会提示输入 API 地址（缺省用当前 provider 的 base_url），确认后 CLI 向 `{地址}/models` 发起 `GET`（当前 provider 已配置密钥时自动携带 `Authorization: Bearer`），把返回的模型列表渲染成勾选清单。交互：上下方向键移动、输入字母实时过滤模型、`空格` 勾选/取消、`a` 全选、`n` 取消全选、`r` 重新输入地址拉取、`Enter` 添加所选、`Esc` 退出。`Enter` 后所选模型写入 `~/.nexuscli/models.json` 的 Custom 列表（同名同地址自动去重）并立即切换到第一个选中模型；模型的上下文窗口优先取响应里的 `context_length` 字段，缺省 128K。地址不可达、HTTP 错误或响应不是标准 `{"data": [...]}` 格式时在选择器内给出可读错误，按 `r` 可重试。

## /skill load 与一次性注入

`/skill load <name>` 把指定 Skill 的正文强制注入你的下一条消息（一次性消费）：下一回合模型会先读到该 Skill 再继续任务，适合主动确保某个技能被加载。

## /usage 双口径

`/usage stats [N天]` 汇总本地 SQLite 用量库 `~/.nexuscli/usage.db`：近 N 天（缺省 7 天）的回合数、总 Token、总成本（USD/CNY）、按模型分布与按来源分布（By source）。落库为 best-effort：普通 REPL 回合（含自定义命令回合）记为 `source=repl`，值为该回合主代理自身的用量；`task` 工具委派的子代理与 `/workflow`、`/expert` 派发的工作流步骤，只要真实产出了 token，也各自落一行 `source=subagent`（记录器在 `run_subagent` 的 done 事件处，零 token 的空跑不落行），所以统计值覆盖 REPL 直连对话与委派回合两类；team/plan-and-execute 模式的 worker 回合与单次 `-p` 模式不落库。保留窗口：`usage.db` 按 90 天保留窗口清理，`stats` 查询时顺带 best-effort 删除窗口外旧行（`UsageStore.prune`），库体积不随使用时长无界增长。

不带参数的 `/usage` 打印一张四行状态表而不是 JSON dump：`turn`（本回合 input/output）、`session`（历史累计回合数与总 Token）、`cost`（有成本数据时按币种列出）与 `note`（内置价格表为带日期的默认值）。底层仍是 `session_usage` 双口径：`historical_total`（恢复基线 + 本进程累计，两者之和等于当前转录的 usage 台账）与 `current_process`（仅本进程记录的回合），并附 `sources` 标注来源，供 SDK 与单次 `--json` 消费。`/clear`、`/resume`、`/fork` 都会同步重置基线与本进程累计，保证双口径始终对应当前转录。

## /history 跨会话检索

`/history <query> [--all]` 在会话转录正文里做大小写不敏感的关键词检索，按更新时间倒序、每会话最多一条命中（取最早命中的消息片段），缺省只搜当前项目，`--all` 搜全部项目；结果表附会话 id 与片段，可 `/resume <id>` 直接跳回。检索基于转录内的消息正文，usage 记录行与损坏行会被跳过；空参数只打印用法提示。

## /fork 分叉会话

`/fork [title]` 把当前会话转录复制为一个新会话并切换过去继续：新会话携带全部历史消息，元数据记录来源 `forked_from`，原会话保持不变；省略 title 时继承源会话标题。两点如实说明：会话目标（`/goal`）按 session id 存储且不随 fork 复制；fork 时会清空排队中的一次性注入（`/skill load` 的正文），新会话从干净状态开始。

## /goal 会话长程目标

`/goal set <text>` 给当前会话设置一句长程目标，之后每个回合开始前都会把 `[session goal] <目标>` 注入用户消息，直到 `/goal clear`；`/goal show` 查看。目标按会话存储在 `~/.nexuscli/goals/<session_id>.json`，只存文本。

## /effort 推理力度

`/effort off|minimal|low|medium|high|xhigh|max` 在会话内即时切换推理力度：带参数直接设定，无参数循环切到下一档（未设置时从 `low` 起步），当前档位以 `模型名:档位` 的形式显示在状态栏。`off` 表示完全不带 `reasoning_effort` 字段发送。该字段只对白名单内的 chat-completions provider（`openai`、`openai-compatible`、`compatible`）随请求发送 `reasoning_effort`；deepseek / glm / kimi 等其他 provider 不支持、不会发送该字段，anthropic provider 会提示不支持。切换模型（`/model`）会创建新客户端并重置 effort。

## /workflow 与 /expert

`/workflow run <file>.json` 加载 JSON 步骤图并按依赖批次并行派发子代理执行（上界 `workflow.max_parallel_steps`，默认 3）；`/expert <topic>` 是同一引擎的内置三步专家流程（调研 → 实施 → 复核）。JSON schema、校验规则与执行边界见 [workflows.md](workflows.md)。

## /compact 手动压缩

```text
/compact                 # 立即压缩一次当前会话历史
/compact 权限规则的实现细节  # 指定保留重点，摘要会优先保留相关内容
```

`/compact` 走与自动压缩相同的确定性流程（不额外调用 LLM）：近端消息原样保留、旧轮次汇总为摘要，并输出压缩统计（tokens before/after 与被摘要的消息条数），方便确认压缩收益。历史为空时会友好提示，不会报错。自动压缩的触发阈值、microcompact 与 provider 超限重试的完整口径见 [memory-context.md](memory-context.md)。

## /turn-resume 回合级恢复

`/turn-resume`（无参数）列出当前工作区的回合 checkpoint；`/turn-resume <id>` 接续一个被
中止的 react 回合：从 checkpoint 重建对话历史并**沿用原 run_id**，在途工具调用按原始 id
经工具日志分流（已完成 → 复用结果；结果未知 → fail-closed 不自动重放），随后从本轮继续。
checkpoint 每 react 轮写两次（执行前 in-flight / 轮末覆盖）；明文落盘于
`~/.nexuscli/turn_checkpoints/`，可能含工具结果中的文件内容。REPL 的 Ctrl+C 仍是中止——
本命令的价值是中止后可从最近完整轮接续。
