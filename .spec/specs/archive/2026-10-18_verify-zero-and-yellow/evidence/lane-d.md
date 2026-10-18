# Lane D 技能与插件扩展 — 真人模拟运行证据

日期:2026-10-18。所有真实运行均:隔离 `HOME`(mktemp 拷贝 `~/.nexuscli/config.json` + `models.json`)、在一次性 scratch 项目目录以 pty 驱动真实 `.venv/bin/nexuscli` 进程、真实网关(cbcn/glm-5.3-flash, localhost:20128)。运行脚本为一次性脚本(/tmp/lane_d/),未提交。

机器验证新文件:`tests/test_lane_d_extensions.py`(4 个测试,仅覆盖矩阵标注"机器+真实"的 12.3/12.5;其余表面已有断言不重写)。

```
$ .venv/bin/python -m pytest -q tests/test_lane_d_extensions.py
....                                                                     [100%]
```

---

## 12.1 Skill 三层(builtin/user/project,user 层覆盖 builtin 层)

怎么跑:pty 起真实 REPL(HOME=隔离目录,项目含 `.nexuscli/skills/team-standard`);预置:用户层 `$HOME/.nexuscli/skills/web-access/SKILL.md`(与 builtin 同名,description 指示"回复第一行输出 WEB-ACCESS-USER-LAYER")、用户层 team-standard、项目层 team-standard(同名,v1.1.0)。

关键输出(`/skill` 表格 + `/skill show`):
```
community-pack  user    on      Community skill installed at the Agent Skills standard location (~/.agents/skills).
team-standard   project on      PROJECT 层团队规范:回答末尾追加一行 PROJECT-LAYER-WINS
web-access      user    on      用户层覆盖版:加载后回复第一行必须输出 WEB-ACCESS-USER-LAYER
```
`/skill show team-standard` 渲染的是**项目层**正文(v1.1.0,PROJECT-LAYER-WINS)——同名链 project > user。网关回合:`请用 load_skill 工具加载 web-access 技能` → 工具调用 + 模型回复:
```
⏺ WEB-ACCESS-USER-LAYER
  已成功加载 web-access 技能(用户覆盖版)。按该技能指示,本回复第一行已原样输出标记 WEB-ACCESS-USER-LAYER。
```
→ **user 层真实覆盖了 builtin 层**(加载进模型上下文的是用户版正文,行为随之改变)。

结论:三层加载、同名覆盖链、真实加载全部正确。矩阵建议:12.1 真实列 ✅。

## 12.2 Agent Skills 规范兼容

怎么跑:同一会话。社区规范格式技能放在规范标准位置 `~/.agents/skills/community-pack/SKILL.md`,frontmatter 含 `allowed-tools: Read, Grep` 与 `disable-model-invocation: true`。

关键输出(上方表格第 1 行):`community-pack user on …`;`/skill show community-pack` 完整渲染 frontmatter+正文(见 /tmp/lane_d/a/out_local.txt)。该技能按规范字段被解析(`disable-model-invocation` 生效时它不进自动候选,但 `/skill` 列表与 show/load 可用),原生目录与规范目录同时扫描无冲突。

结论:社区 skill 放规范位置直接可用。矩阵建议:12.2 真实列 ✅。

## 12.3 自定义斜杠命令(机器+真实)

机器:`tests/test_lane_d_extensions.py::test_custom_command_trigger_applies_hints_and_restores` + `::test_custom_command_plan_mode_active_during_run_only` — 走 repl.py:326-335 的真实派发路径(`_match_custom_command` → `_run_custom_command`):展开提示词到达 agent、`mode: plan` 运行期间把 permission controller 切到 plan(`config.policy.plan_mode is True`)、`allowed-tools` 收窄注册表、运行后全部还原;断言输出/状态变化。4/4 通过。

真实:项目 `.nexuscli/commands/release-note.md`(frontmatter description/argument-hint,正文 `请严格只回复一行,格式:RELEASE-CMD-OK <$ARGUMENTS>…`),REPL 敲 `/release-note v9.9.9`:
```
⏺ RELEASE-CMD-OK <v9.9.9>
```
提示词经真实网关生效,$ARGUMENTS 替换正确。

结论:一致。矩阵建议:12.3 两列 ✅。

## 12.4 插件(.nexuscli/plugins/,agents+skills+commands)

怎么跑:`$PROJ/.nexuscli/plugins/hello-pack/` 放最小插件(plugin.json + skills/pack-skill + commands/pack-hi.md + agents/pack-agent.md),pty REPL 三连。

关键输出:
```
pack-skill      project on      插件自带技能:加载后回复第一行输出 PACK-SKILL-LOADED   ← /skill 列表
⏺ PACK-CMD-OK from hello-pack                                            ← /pack-hi(插件命令)
⏺ task(agent_type=pack-agent, description=自检, prompt=执行自检)
  ⎿ task: Subagent report (pack-agent):  (2 lines)
⏺ PACK-AGENT-OK from hello-pack。                                         ← 插件 agent 被选中并执行
```
(另一次会话中模型还通过 load_skill 真实加载了 pack-skill 并输出 PACK-SKILL-LOADED,见 /tmp/lane_d/c/out_agent.txt。)

结论:三类组件在真实进程全部生效。矩阵建议:12.4 真实列 ✅。

## 12.5 自定义子代理(机器+真实)

机器:`tests/test_lane_d_extensions.py::test_project_reviewer_agent_selected_by_task_tool` — `.nexuscli/agents/reviewer.md` 经 `task` 工具选中(fake LLM 捕获 system prompt 含 md 正文、registry 白名单生效、无 task 工具);`::test_task_tool_unknown_type_lists_custom_reviewer` — 未知类型报错文案列出项目 reviewer。4/4 通过。

真实:`.nexuscli/agents/reviewer.md`(指示"报告必须以 REVIEW-VERDICT: 开头"),REPL 让主代理调 task 工具:
```
⏺ task(agent_type=reviewer, description=审查 calc, prompt=审查 src/calc.py,…)
  ⎿ task: Subagent report (reviewer):  (2 lines)
⏺ 子代理(reviewer)报告原文如下:
▌ REVIEW-VERDICT: 有问题需修改 —— div() 未处理除零情况,b 为 0 时会抛出未捕获的 ZeroDivisionError(add 无问题)。
```
子代理遵循 reviewer.md 的报告格式约束,审查结论与代码事实相符。

结论:一致。矩阵建议:12.5 两列 ✅。

## 6.8 Hooks 七事件(真实 hook 脚本)

怎么跑:用户级 config.json 配全部七事件,每事件一条真实 shell hook(`echo <Event> >> hooklog.txt`);pty REPL 真实会话:启动(SessionStart)、`用 list_dir 列出当前目录`(UserPromptSubmit/PreToolUse/PostToolUse/Stop)、`用 read_file 读 missing-file-xyz.txt`(失败 → PostToolUseFailure)。

hooklog.txt 实测计数(`sort | uniq -c`):
```
   2 PermissionRequest
   1 PostToolUse
   1 PostToolUseFailure
   2 PreToolUse
   1 SessionStart
   2 Stop
   2 UserPromptSubmit
```
七类事件全部由真实 hook 脚本触发;失败回合用户可见输出正确(`⏺ 工具返回原样如下: Not a file: /private/tmp/lane_d/e/proj/missing-file-xyz.txt`)。

结论:七事件在真实会话全部到齐。矩阵建议:6.8 真实列 ✅。

## 6.9 工作区 hooks 信任门禁

怎么跑:模拟 clone:新隔离 HOME(无信任记录)+ "别人仓库"(项目 `.nexuscli/config.json` 只有 hooks.PreToolUse → `echo pre-tool-ran >> marker.txt`)。会话 1 门禁提示按 `n`;会话 2 按 `y`。

关键输出:
```
Workspace hooks detected /private/tmp/lane_d/f/proj (fingerprint f2f344f1af05dba7)
Trust workspace hooks? [y/n] (n): n
Workspace hooks disabled for this session (user-level hooks unaffected).
Trust workspace hooks? [y/n] (n): y
Workspace hooks trusted (remembered for this workspace).
```
实测:拒绝后触发工具调用,marker.txt **未产生**(hook 未执行);信任后同样调用,marker.txt 产生且内容 `pre-tool-ran`;`~/.nexuscli/hooks-trust.json` 持久化完整 sha256 指纹与 trusted_at。

结论:未信任不执行、信任后执行、信任记忆落盘,全部正确。矩阵建议:6.9 真实列 ✅。

## /init 生成 AGENTS.md

怎么跑:scratch 项目(README.md="Demo Shop 命令行商店示例"、src/app.py 入口打印 shop demo),pty REPL `/init`;首个 bash 审批提示按 `a` 切 Auto(真人会做的选择),完成后自动写文件。

关键输出:
```
⏺ AGENTS.md 已生成…
• 工作区仅含 src/app.py 与 README.md,无 pyproject.toml/package.json/Makefile 等构建配置;
• …(仅登记了实际验证过的 python3 src/app.py,退出码 0,输出 shop demo)
```
AGENTS.md 实际内容与仓库相符:项目名取自 README、入口 `python3 src/app.py` 为模型实跑验证(rc=0)、目录结构、如实注明无测试/lint 工具链、协作约定。全文见 /tmp/lane_d/g/proj/AGENTS.md。

结论:内容与仓库实际一致,无编造。矩阵建议:/init 真实列 ✅。

## /expert 内置三步专家流程

怎么跑:同项目,`/hitl auto` 后 `/expert 本仓库入口文件的输出是什么`(规模最小化:纯只读主题)。

关键输出:
```
[workflow] (1/3) dispatching step research
[workflow] (2/3) dispatching step execute
[workflow] (3/3) dispatching step report
# 步骤报告:report(第 3/3 步)—「本仓库入口文件的输出是什么」最终综合报告
## 一、最终结论 … `stdout='shop demo\n'`,`stderr=''`,`rc=0`
```
三步链在真实网关走通,report 步独立复跑交叉验证并给出结论、依据、缺口(详见 /tmp/lane_d/g/out_expert2.txt)。

结论:三步流程真实网关走通,输出可读正确。矩阵建议:/expert 真实列 ✅。

## /goal 跨回合长程目标

怎么跑:pty REPL `/goal set 从现在起每次回复的第一行必须原样输出【GOAL-ON-DUTY】`,然后 `/goal`,再发下一回合问题。

关键输出:
```
Session goal set: 从现在起每次回复的第一行必须原样输出【GOAL-ON-DUTY】
It will be prepended to every turn of this session.
(裸 /goal 输出: Usage: /goal set <text> | /goal show | /goal clear)
⏺ 【GOAL-ON-DUTY】当前目录是 /private/tmp/lane_d/h/proj,一个位于系统临时目录下的项目工作目录(未调用工具,故不对其具体内容作判断)。
```
目标文件持久化:`~/.nexuscli/goals/20261018-…json`(`{"goal": "…【GOAL-ON-DUTY】", "updated_at": …}`)。下一回合(同会话)回复第一行确实是【GOAL-ON-DUTY】——目标仍在且改变行为。

结论:设置、持久、逐回合注入、影响输出,全部验证。矩阵建议:/goal 真实列 ✅(裸 `/goal` 显示 usage 属设计行为,查看目标用 `/goal show`)。

## load_skill / save_skill(重启后仍可加载)

怎么跑:会话 1(隔离 HOME):让模型用 save_skill 工具在 user scope 存技能 cat-style → 审批提示按 `y`;退出进程。会话 2(**新进程**):`/skill show cat-style`,再让模型用 load_skill 加载并问好。

关键输出:
```
⏺ save_skill(…)
  ⎿ save_skill: Saved skill "cat-style" in user scope at /private/tmp/lane_d/i/home/.nexuscli/skills/cat-style/SKILL.md.
== 新进程 ==
(/skill show cat-style 完整渲染已落盘的 frontmatter+正文,状态栏 skills 由 1 变 2)
⏺ load_skill(name=cat-style)
  ⎿ load_skill: Loaded skill "cat-style" instructions for the next model turn.
⏺ CAT-STYLE-ON
  你好呀,喵~ 🐾
```
技能跨进程持久(文件落盘)、新进程可 show、load_skill 注入后模型按技能指示输出 CAT-STYLE-ON。

结论:工具保存→重启→工具加载全链路正确。矩阵建议:load_skill/save_skill 真实列 ✅。

---

## 汇总

| 矩阵行 | 机器 | 真实 | 建议 |
|---|---|---|---|
| 12.1 Skill 三层 | test_skill.py(已有) | 本轮 pty+网关 | ✅ |
| 12.2 规范兼容 | test_skill_spec_compat.py(已有) | 本轮 pty | ✅ |
| 12.3 自定义命令 | 本轮新增 2 断言 | 本轮 pty+网关 | ✅ |
| 12.4 插件 | test_plugins.py(已有) | 本轮 pty+网关 | ✅ |
| 12.5 自定义子代理 | 本轮新增 2 断言 | 本轮 pty+网关 | ✅ |
| 6.8 hooks 七事件 | test_hooks.py(已有) | 本轮真实 hook 脚本 | ✅ |
| 6.9 信任门禁 | test_hooks_trust.py(已有) | 本轮 n/y 双会话 | ✅ |
| /init | test_init_command.py(已有) | 本轮 pty+网关 | ✅ |
| /expert | 无(仅真实) | 本轮 pty+网关 | ✅(真实列) |
| /goal | test_goal.py(已有) | 本轮 pty+网关 | ✅ |
| load_skill/save_skill | test_skill.py(已有) | 本轮跨进程双会话 | ✅ |
