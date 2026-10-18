# Lane C 证据 — team / task / plan / workflow 表面收口

> 2026-10-18。所有真实运行均隔离 HOME(`RUNHOME=$(mktemp -d)` + 复制 `~/.nexuscli/config.json`、`models.json`),
> 真实网关 `http://localhost:20128/v1`(cbcn/glm-5.3-flash)。一次性 pty 驱动脚本在 `/tmp/laneC-scripts/`(未提交)。
> 机器验证:`tests/test_lane_c_modes.py`(2.7)、`tests/test_lane_c_team_task.py`(4./task)。
> 命令:`.venv/bin/python -m pytest -q tests/test_lane_c_team_task.py tests/test_lane_c_modes.py` → 3 passed;
> 回归:`tests/test_multi_agent.py test_plan_mode.py test_cli.py test_plan.py test_plan_checkpoint.py test_subagent.py test_subagent_mailbox.py test_bg_subagent.py test_query.py` → 79 passed。

---

## 1. [4./team 与 2.6] REPL 内 /team(真实网关,最小规模)

怎么跑:pty 驱动真实 REPL,输入 `/team --mode plan 规划一个仅一步的嵌套 DAG,该步骤以 plan 模式执行:尝试在当前目录创建文件 nested_plan_probe.txt(内容 PROBE),然后报告这次写入是被允许还是被拒绝。`

关键输出(REPL 内,已去 ANSI):
```
⏺ Phase 1: planner
  Execution plan:
   • [step_1] 以 plan 模式执行嵌套 DAG：… (nested_dag, mode: plan, deps: none) Phase 2: workers and reviewer
  Multi-Agent task completed.
  Execution summary:
   • [step_1] COMPLETED: … Result: 正在规划任务：…（checkpoint 已保存：plan_1792327077532_9f016ec4…
```
工作区探测文件未创建;嵌套 checkpoint(`$RUNHOME/.nexuscli/plan_runs/plan_1792327077532_9f016ec4.json`)完整保留了 plan worker 的报告(见第 2 节摘录)。

结论:REPL 内 `/team` 真实跑通,Phase 1/2 结构与 "Multi-Agent task completed" 摘要对用户可读;规模最小(1 步)。
矩阵建议:`/team` ✅(REPL 内已跑);2.6 维持 ✅。

## 2. [2.7] team worker-mode=plan 写工具硬拒(机器+真实)— 发现并修复一个真缺陷

缺陷:worker 以 plan 模式运行时只走嵌套 PlanExecuteAgent,从不置位 `policy.plan_mode`,执行器的 plan 只读闸门(tools/executor.py:126)根本不生效——plan worker 可以写文件。`-p --mode plan` 有同样的门且已修过(cli.py:228-234),team worker 漏掉了。

真实证据(修复前的一次 `-p --mode team --worker-mode plan` 探测里,planner 给写步骤显式选了 `mode: react`,写入被 -p 审批策略兜住;真正走进 plan worker 的路径无任何门)。修复后真实 REPL 运行(同第 1 节那次),嵌套 plan worker 的报告(磁盘 checkpoint 原文摘录):
```
| 1 | `write_file` 创建 `nested_plan_probe.txt`（内容 `PROBE`） | ❌ 被拒绝 |
> `Tool "write_file" is not allowed in plan mode. Only read-only tools are permitted. Press Shift+Tab to switch back to default mode and run the plan.`
即：当前会话处于 **plan（计划）模式**，… 所有写入类工具…均被权限层直接拦截。
**最终状态：写入尝试 → 被权限系统拒绝 → 磁盘无变更 → 行为符合 plan 模式设计预期。**
```
(模型还尝试用 bash 绕行,同样被拦;再用只读工具确认文件不存在。工作区无探测文件。)

修复:src/nexuscli/agent/orchestrator.py `SubAgent._execute_plan` 给嵌套 PlanExecuteAgent 传 `replace(config, policy=replace(config.policy, plan_mode=True))` 的**每运行配置副本**——不改动共享 config,避免并行 react worker 被误伤门。
回归:tests/test_lane_c_modes.py `test_team_plan_worker_write_is_hard_denied` 驱动生产路径(SubAgent→PlanExecuteAgent→query→executor),断言模型收到 plan 闸门拒绝文案、文件未落盘、共享 config 的 plan_mode 保持 False。修复前该测试红(收到的是审批拒绝文案),修复后绿。

矩阵建议:2.7 ⬜→✅(机器+真实都有)。

## 3. [4./task] REPL /task 派任务并拿到结果(机器+真实)

机器:tests/test_lane_c_team_task.py — 队列生命周期用户可见文案(“Queued task_… (plan). Run \`nexuscli worker\` or \`nexuscli serve\` to consume it.”、列表行、log/cancel、坏 mode 报 “mode must be one of: plan, react, team”)。2 passed。

真实(三段,全部真实进程):
1. REPL pty:`/task add --mode react 用 list_dir 查看当前目录,然后只回答:目录里有几个条目。` →
```
Queued task_20261018124435764950_e07895 (react). Run `nexuscli worker` or `nexuscli serve` to consume it.
/task → task_20261018124435764950_e07895 queued react attempts=0 用 list_dir 查看当前目录…
```
2. 真实 `nexuscli worker --workers 1` 子进程消费(轮询 tasks.db 到终态):`FINAL: … completed / RESULT: 0 个条目。`(空目录,事实正确)
3. 新 REPL 会话:`/task` 显示 `completed attempts=1`;`/task log <id>` → `0 个条目。`

结论:入队→worker 消费(真实网关)→跨会话取回结果,全链路输出可读。矩阵里 “/task 派子代理” 的描述与实现有出入:REPL `/task` 是持久任务队列(子代理派发是第 5 行的 `task` 工具)。
矩阵建议:`/task` ⬜→✅。

## 4. [5.] task 工具:模型派子代理查事实(真实)

怎么跑:`HOME=$RUNHOME nexuscli -p "用 task 工具派一个 general-purpose 子代理去查:tests/test_render.py 里第一个测试函数叫什么名字…"`(仓库根,21.7s)。

关键输出:
```
子代理(general-purpose subagent)查到的结果是:
**`test_banner_renders_two_line_header`**
(以上名字为子代理报告的原文…位于 `tests/test_render.py` 第 24 行…)
```
ground truth 核对:`grep -n "^def test_" tests/test_render.py` 第一条即 `24:def test_banner_renders_two_line_header():` — 事实正确。

结论:task 工具真实委派、子代理检索、主代理转述,全部正确可读。
矩阵建议:5.(task 工具)🟡→✅。

## 5. [2.8] 工具循环检测止损(真实)

怎么跑:pty REPL,要求模型“连续调用 list_dir 6 次,参数完全相同”。模型单回合并行发出 6 个相同 `list_dir(path=.)` 调用。

关键输出:
```
⏺ list_dir(path=.) ×6
  ⎿ list_dir: Loop detected: the same tool call was repeated again after the loop-detection warning. The call was
    not executed and the turn is ending now. Summarize what was accomplished or state what blocked you.
⏺ Loop detected: the same tool call was repeated consecutively and the turn was terminated after 1 turns
(agent.loop_detection_threshold=3). Raise agent.loop_detection_threshold in config.json to allow more repetitions.
```
结论:阈值 3 触发 nudge,重复后 abort,两条止损信息均可读且给出调参出口;回合干净终止,REPL 可继续。小瑕疵:”after 1 turns” 单复数(低危,见 feedback)。
矩阵建议:2.8 🟡→✅。

## 6. [2.9] provider 超限自动压缩重试(生产路径驱动;真网关撞限不可达)

怎么跑:两次真实网关探测都无法撞出 provider 超限——`read_file` 把 600KB、1.4MB(约 150K/350K token)读进上下文,网关均正常返回(“收到”),说明该中转后端窗口 ≥350K token,最小调用代价内构造不出真超限。改用任务书对 2.8 认可的同款构造:一次性脚本驱动**生产路径**(真实 `agent.py` react 循环、真实 ContextWindowManager/压缩器、真实重试),仅在 client 接缝注入 provider 超限错误(首次 chat 返回 `context_length_exceeded`),历史为 6 轮真实体积的 Message。

关键输出:
```
chat calls: 2
overflow notice present: True   ("Provider reported a context-length overflow; compacting the conversation history and retrying once.")
compressed event: True
done event: True
RESULT: PASS
```
结论:压缩→重试→成功完成的机器全链路真实跑通;provider 错误本身无法在本网关真实触发,已在 feedback 如实说明。
矩阵建议:2.9 🟡→✅(附注:provider 错误为接缝注入)。

## 7. [2.10] /plan 中断续跑(真实)

怎么跑:pty REPL `/plan 任务A(list_dir)→任务B(bash uname,依赖A)→任务C(汇总,依赖B)`;监控输出,task_1 完成且 task_2 运行中时发 Ctrl+C;然后同会话 `/plan --resume <id>`。

关键输出:
```
已完成 [task_1]：…任务A执行完成。 … ⎿ Running task_2 …  ^C
Interrupted — turn aborted.
❯ /plan --resume plan_1792328300683_dd875c32
⏺ 恢复计划 plan_1792328300683_dd875c32：…任务数：3 … 当前可执行：1
  开始执行计划……
  ⎿ Running task_2  基于任务A的结果，使用 bash 运行 uname 命令获取系统信息
```
checkpoint 落盘核验:`plan_runs/plan_1792328300683_dd875c32.json` 最终 `status: COMPLETED, task_1/2/3 全 COMPLETED`;resume 只跑 task_2/task_3(未重跑 task_1),输出 “计划执行完成”。

结论:中断保进度、resume 从 checkpoint 续跑,全部正确。小瑕疵:resume 的 plan 摘要行 “首批任务:task_1” 未反映 task_1 已完成(低危,见 feedback)。
矩阵建议:2.10 🟡→✅。

## 8. [4./workflow 与 12.6] 最小 JSON 步骤图真实跑通(真实,兼 12.7)

怎么跑:工作区放 wf.json(3 步:alice、bob 无依赖并行;merge 依赖两者),pty REPL `/workflow run wf.json`。

关键输出(REPL 内):
```
[workflow] (1/3) dispatching step alice
[workflow] (2/3) dispatching step bob
[workflow] (3/3) dispatching step merge
[workflow] Workflow complete — 3 step reports:
=== alice === … 用 mailbox_post 向 general-purpose.bob 发送数字 20(message_id 49b1ce2609b5)… 收到 22 … 20 + 22 = 42。
=== bob === … 将数字 22 发送给 …general-purpose.alice … 收到 20 … 22 + 20 = 42。
=== merge === … 两个和均为 42,交叉一致。
```
磁盘核验:`$RUNWS/.nexuscli/mailbox/<session>/general-purpose.{alice,bob}.jsonl` 存在且已清空(drain 语义)。

结论:JSON 步骤图并行分批调度、报告汇总、输出全部可读。
矩阵建议:/workflow 🟡→✅;12.6 🟡→✅。

## 9. [12.7] 子代理邮箱协作(真实,与第 8 节同一次运行)

alice/bob 两个并发子代理经 mailbox_post/mailbox_read 完成“交换数字并求和”这一件事:投递回执 message_id 双向对上(49b1ce2609b5、4a6212ee5871),两边独立算出 42,merge 步骤交叉验证一致。报告全文见第 8 节摘录。
矩阵建议:12.7 🟡→✅。

附注(观察,非缺陷):general-purpose 子代理有时会先用 `bash ls … && cat …` 探测 `~/.nexuscli/agent-memory/…`,触发 outside-write-scope 审批(见 feedback #1);本次成功运行中未触发,且记忆目录不可写时子代理正确按降级指令把备忘并入报告(alice 报告末段)。

## 10. [12.8] 后台子代理 + 完成播报(真实)

怎么跑:pty REPL,要求模型用 task 工具 `run_in_background: true` 派 general-purpose 子代理算 7×6;随后追问一次以触发回合末播报。

关键输出:
```
⏺ Started background subagent task 07cc490503bd (general-purpose). Report file: /tmp/laneC-home.h6S1/.nexuscli/…
(追问回合)⏺ task_output(tail_bytes=256, task_id=07cc490503bd)
  ⎿ task_output: [completed] report_path=…07cc490503bd.md  (2 lines)
⏺ 跑完。
[bg-subagent] completed general-purpose "bg-probe" (task 07cc490503bd) — report: /tmp/laneC-home.h6S1/.nexuscli/bg-subagents/07cc490503bd.md
```
磁盘核验:`$RUNHOME/.nexuscli/bg-subagents/07cc490503bd.md` 内容 `42`。

结论:派发不阻塞、task_output 可查、完成播报一次性可读、报告落盘正确。
矩阵建议:12.8 🟡→✅。

---

## 本轮修复(fixes)

| 位置 | 内容 |
|---|---|
| src/nexuscli/agent/orchestrator.py `SubAgent._execute_plan` | worker plan 模式从未置位 `policy.plan_mode`,嵌套 plan 运行可写文件;现传入 `plan_mode=True` 的每运行配置副本(不污染共享 config),写工具被执行器硬拒。回归:tests/test_lane_c_modes.py(修复前红/修复后绿) |
| tests/test_lane_c_modes.py(新) | 2.7 机器断言 |
| tests/test_lane_c_team_task.py(新) | 4./task 机器断言 |

## Feedback(跨边界/未收敛)

1. **[high] tools/builtins.py:627-673 `_reject_outside_write_scope` 无视 `permissions.allow` 规则**:配置 `allow: ["bash"]` 后,`ls X 2>/dev/null && cat Y` 仍弹 HITL 审批(两次 workflow 真实运行被该提示卡住;executor 直接复现:`prompted: 1`,文案 “Command rejected by write-scope approval. Static analysis found a write target outside the workspace: /dev/null, /dev/null”)。`/dev/null` 被当成“工作区外写目标”——凡带 `2>/dev/null` 的命令都会触发。建议:该守卫先过 `evaluate_permissions`(allow 则跳过)并把 /dev/null 列为豁免。builtins.py 对本 lane 只读,未改。
2. **[low] `--worker-mode plan` 可被 planner 静默覆盖**:team planner 给写步骤显式输出 `"mode": "react"`(2.7 修复前探测实测),用户要求的 plan worker 未生效且无提示。orchestrator 的 planner 角色提示/parse_plan 语义属产品决策,交回 owner。
3. **[low] 真网关无法最小代价构造 provider 超限**:cbcn 中转后端实测吞下 600KB 与 1.4MB 上下文均不报错;2.9 的真实重试证据来自生产路径驱动+接缝注入错误(见第 6 节)。
4. **[low] `/plan --resume` 摘要行未反映已完成任务**:“任务数:3 | 当前可执行:1 | 首批任务:task_1” 在 task_1 已 COMPLETED 时仍如此显示(实际正确从 task_2 续跑)。plan/models.py `summarize()` 文案,低危。
5. **[low] 循环止损文案单复数**:“terminated after 1 turns”(loop_guard.py:41-47),低危。
