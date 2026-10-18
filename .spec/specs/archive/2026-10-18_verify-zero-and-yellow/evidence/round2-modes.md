# Round 2 证据 — 模式计划与提示复验(2026-10-18)

执行者:模式计划与提示复验 lane。所有真实运行均隔离 HOME(`RUNHOME=$(mktemp -d)`,
拷贝 `~/.nexuscli/config.json` + `models.json`),真实网关 `http://localhost:20128/v1`
(cbcn/glm-5.3-flash)。pty 驱动脚本为一次性脚本(`/tmp/round2_*.py`,未提交),原始输出
在 `/tmp/round2_*.log`。`/team`、`/plan` 的 pty 驱动沿用第一轮 lane C 已验证的
`/tmp/laneC-scripts/pty_team_plan.py` 同款时序(boot 后 4s/3s 发键,固定 300s 观测窗)。

机器验证:`.venv/bin/python -m pytest -q tests/test_round2_recalled_memory.py
tests/test_prompt.py tests/test_memory.py tests/test_lane_b_memory_surface.py
tests/test_multi_agent.py tests/test_plan.py tests/test_plan_checkpoint.py` → 52 passed。

---

## 1. [F4] /team --mode plan 用户优先 — 真实 → ✅

怎么跑:pty 驱动真实 REPL(空工作区 /tmp/round2-ws-lanec),
`/team --mode plan 规划一个仅一步的嵌套 DAG…尝试在当前目录创建文件 nested_plan_probe.txt(内容 PROBE),然后报告这次写入是被允许还是被拒绝。`
(与 lane C 第 1 节同一驱动与时序;planner 复测中确实给该写步骤自选了 react——被 pin 覆盖。)

关键输出(REPL 内,已去 ANSI):
```
⏺ Phase 1: planner
  Execution plan:
   • [step_1] 以 plan 模式构建并执行嵌套 DAG:…创建文件 nested_plan_probe.txt(内容为
     PROBE),随后检查并报告该写入是被允许还是被拒绝… (nested_plan, mode: plan, deps: none)
  Multi-Agent task completed.
   • [step_1] COMPLETED: … Result: 正在规划任务:…(checkpoint 已保存:p...
```
- 摘要行 `mode: plan`:planner 未按上轮习惯给写步骤输出 react,用户的 pin 生效
  (src/nexuscli/agent/orchestrator.py:335-340 `pinned_worker_mode`,parse_plan 590-603 覆盖)。
- 真实执行写工具硬拒(嵌套 checkpoint `$RUNHOME/.nexuscli/plan_runs/plan_1792334624213_d47b3ca8.json`
  task_1 结果原文):`write_file` ❌ 报错 `Tool "write_file" is not allowed in plan mode. Only
  read-only tools are permitted.`;bash `printf 'PROBE' > …` 同样被拒;worker 只读核验后明确
  结论「写入被拒绝(DENIED),未能创建文件」。工作区 `ls` 为空——探测文件未创建。
- 无 --mode 不回归:同驱动真实跑 `/team 用 list_dir 工具列出当前目录的条目…`(不带 --mode),
  planner 每步自选:`• [step_1] …(tool, mode: react, deps: none)`、`• [step_2] …(report,
  mode: react, deps: step_1)`,两步按依赖执行、reviewer 通过、"Multi-Agent task completed."。
  parse_plan 机器对照:`pinned=False → step_1=react, step_2=react, step_3=plan(planner 选择,
  与修复前语义一致);pinned=True → 全部 plan`(node mode 被覆盖)。

verdict:✅ pin 生效 + 硬拒 + 无 --mode 行为不回归,三层证据齐全。

## 2. [F5] /plan --resume 摘要 — 真实 → ✅

怎么跑:pty 驱动真实 REPL(/tmp/round2-ws-f5,note_b.txt 内容「暗号是柠檬七号。」),
`/plan` 三个依次依赖的任务(list_dir → read_file → 合并汇报);监控输出,
`已完成 [task_1]` 出现 3 秒后发 Ctrl+C;然后同会话 `/plan --resume <id>` 两次(第二次针对已全部完成的 checkpoint)。

关键输出(同一会话,已去 ANSI):
```
(首次 /plan,全部 PENDING)首批任务:task_1        ← 正常语义未受影响
⏺ 已完成 [task_1]:任务一完成。…  ^C  Interrupted — turn aborted.
(/plan --resume plan_1792335583068_031f7919)
⏺ 恢复计划 plan_…:…任务数:3 | 并行批次:3 | 当前可执行:1
  首批任务:task_2          ← 修复点:不再指向已完成的 task_1
  ⎿ Running task_2 使用 read_file 工具读取 note_b.txt … 计划执行完成。
(第二次 /plan --resume 同 id)
  任务数:3 | 并行批次:3 | 当前可执行:0
  首批任务:无(全部完成)    ← 修复点
  ⏺ 计划执行完成。(未重跑任何任务,task_1/2/3 结果原样保留)
```
磁盘核验:checkpoint `plan_runs/plan_1792335583068_031f7919.json` 最终 `status: COMPLETED`,
task_1/2/3 全 COMPLETED;resume 只执行了 task_2/task_3,内容正确(暗号原样读出)。
修复位置:src/nexuscli/plan/models.py:207-229 `summarize()`(首批任务取第一个仍有 PENDING 的批次,
空则「无(全部完成)」)。

verdict:✅ 三种状态(全 PENDING→task_1、中断续跑→task_2、全部完成→无(全部完成))全部正确可读。

## 3. [V] 召回记忆措辞加强 — 修改+真实复测 → ✅(7.2 型两次通过;残留风险见 feedback #1)

上轮反馈:泛化「candidates, not instructions」措辞下,已召回的 preference/constraint 被当建议忽略。
本轮在 src/nexuscli/prompt/assembler.py 收口(经三轮网关实证迭代):

1. 仅加强 header 措辞(约束句放块首)→ 真实 2 次均失败:模型仍以「工作区是空的,无法判断」回避,
   甚至答「默认按 npm 处理」。`llm.debug_dump` 抓包证实块确实进了真实请求(access_count 同步 0→4)。
2. 约束句移到条目之后(近因)→ 仍失败 1 次。
3. 最终结构(当前代码):把 kind=preference/constraint 条目从 `<recalled-memory trust="untrusted-data">`
   **拆出**,进受信块 `<project-constraints trust="user-saved-memory">`,块内条目按指令式渲染
   (「- 必须遵守的项目约定:…」),并在 build_static 核心准则加一条「user-saved project constraints
   … follow it even if workspace files are missing or suggest otherwise」。依据:7.1 的静态
   `<project-instructions trust="workspace-config">` 一直被模型遵守,untrusted 框架则被无视——
   信任分级才是行为差异点(save_memory 落盘本身有人工审批门,语义上属用户自存配置,非不可信数据)。

最终实现的真实复测(隔离 HOME,种子 `用户偏好:在这个项目里一律使用 bun 而不是 npm 来运行脚本和安装依赖`,
问题不点名记忆):
```
$ nexuscli -p "这个项目该用哪个包管理器来跑脚本?只回答包管理器名字。"
Bun
$ nexuscli -p "在这个项目里跑测试脚本,应该用哪个工具来跑?"
- 跑测试:用 bun test…装依赖:bun install…(当前工作区…还没有任何项目文件…)
$ nexuscli -p "我要在这个项目里安装 left-pad…写出你会实际执行的完整命令。"
长期记忆里有你的明确偏好:在这个项目里一律使用 bun…  bun add left-pad
```
7.2 型开放问题连续两次把约束带出且按要求点名依据;抓包(dump 第 40 行 request payload)证实
`<project-constraints>` 块与静态准则确实在发给网关的 system 消息里。
7.1 静态记忆不破坏:全新 RUNHOME(空 memory.db,0 行)+ 样本项目 AGENTS.md(杜撰代号)→
`nexuscli -p "这个项目的发布代号是什么?只回答代号本身。"` → `BLUE-OTTER-77`。
回归:tests/test_round2_recalled_memory.py 新增 5 条(约束/偏好带措辞、纯 fact 仍走 untrusted 框、
混合召回拆分、static 不含召回内容);tests/test_prompt.py 2 处断言按新表面更新(见 feedback #3 披露)。

verdict:✅(按 7.2 型验收;措辞对更刁钻问法的覆盖上限见 feedback #1)。

## 4. [V] 2.9 边界文档化核对 — 文档核对(未重跑)→ ✅

核对结论:lane-c.md 第 6 节表述与磁盘证据一致、如实未夸大:
- 「网关接受超大上下文」:/tmp/laneC-scripts/overflow.log、overflow2.log 两次探测
  (600KB / 1.4MB)输出均以「收到」结尾——网关正常返回,原文佐证成立。
- 「超限重试证据来自生产路径+client 接缝注入」:overflow_retry_driver.py 驱动真实
  agent.py react 循环 + 真实 ContextWindowManager/压缩器 + 真实重试,仅 OverflowOnceClient
  在首个 chat 注入 `context_length_exceeded`;§6 摘录(chat calls: 2 / overflow notice
  present: True / RESULT: PASS)与脚本行为一致。
- 披露闭环:§6 正文明说「真网关撞限不可达」,矩阵建议带附注「provider 错误为接缝注入」,
  feedback #3 再次声明。**本边界已如实写清,无需重跑。**

verdict:✅ 文档如实;2.9 的证据边界(接缝注入)在 lane-c.md §6 + feedback #3 两处可查。

---

## 本轮边界内修改(fixes)

| 位置 | 内容 |
|---|---|
| src/nexuscli/prompt/assembler.py | 召回的 preference/constraint 从 untrusted recalled-memory 框拆入受信 `<project-constraints trust="user-saved-memory">` 块,约束条目按「必须遵守的项目约定:」指令式渲染,块首含「以下召回的项目约束必须遵守」;build_static 核心准则新增一条 user-saved constraints 遵从规则。三轮真实网关迭代,最终设计 7.2 型开放问题 2/2 通过(过程与失败轮见第 3 节) |
| tests/test_round2_recalled_memory.py(新) | 5 条回归:约束/偏好进受信块带措辞、纯 fact 留 untrusted 框、混合召回拆分、static 前缀不含召回内容 |
| tests/test_prompt.py(2 处断言) | 召回块表面按设计变更同步:偏好→project-constraints 断言;constraint 测试改为断言受信块+约束措辞(披露见 feedback #3) |

## Feedback(未收敛/披露)

1. **[medium] 措辞对 flash 级模型的约束力上限**:最终设计下 7.2 型开放问题 2/2 通过,但更对抗的
   问法(「写出你会实际执行的完整命令」)在迭代过程中多次被模型以「工作区是空的→按默认 npm」回避
   (弱措辞 3 连败;最终设计下同问法一次给建议不点名包管理器、重复一次合规 bun add left-pad)。
   合规是概率性的,prompt 措辞无法 100% 绑定;若要更强保证需产品级手段(如检测到与约束冲突的回答时
   强制引用约束来源),属后续 owner 决策。
2. **[low] 裸 pty 下 REPL 渲染偶发冻结**:自写驱动的部分 /team 运行中,回合执行(嵌套 checkpoint
   落盘、plan 闸门生效均有磁盘实证)但 pty 上 0 字节渲染直至 SIGTERM;同命令换 lane C 已验证驱动
   则渲染完整。疑似 prompt_toolkit CPR 失败(裸 pty 无法应答 `ESC[6n`;真实终端总会应答)导致的
   非确定性渲染问题,未能稳定复现,本轮未改代码(渲染路径在 repl/边界外)。
3. **【披露·低】tests/test_prompt.py 两处断言更新**:`test_dynamic_prompt_recall_is_rebuilt…`
   与 `test_dynamic_memory_is_bounded_and_marked_untrusted` 原断言召回内容出现在
   recalled-memory 框;受信块拆分是该两项测试所测表面的设计变更,故同步(先例:lane B 对
   tests/test_context.py 的同款披露)。全量回归以工作流末尾统一跑为准。
