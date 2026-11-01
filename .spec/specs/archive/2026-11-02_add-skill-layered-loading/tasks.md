# Skill L0/L1/L2 分层加载 + real 轨 load_skill 白名单修复 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] Skill 三层模型与调用点（registry 解析 abstract/overview + L1 自动生成兜底；
  load_skill 加 level 参数默认 full；agent.py 候选行 abstract 替换；/skill list 层信息；
  搬运 OpenViking skill_loader.py 校验规则或整搬适配，保留 AGPL 头与 Ported-from）
  - boundary: 只改 src/nexuscli/skill/**、src/nexuscli/tools/builtins.py 的 load_skill、
    src/nexuscli/agent/agent.py 候选行、src/nexuscli/builtin_skills/、
    src/nexuscli/entrypoints/commands/skill.py（/skill list 层信息，本条目第一行点名
    的功能的实现所在）、tests 新增；不改 eval/memory/其他工具
  - verify: `uv run --extra dev python -m pytest -q -k skill`
- [x] real 轨修复与 skill 行为场景（load_skill 入 REAL_TRACK_TOOLS 白名单；白名单测试
  更新；examples/evals/real/03_skill_rule_follow.json——规则含可观测标记的遵循链路
  场景，final_contains 或 judge 断言；对应单测）
  - depends-on: task_0001
  - boundary: 只改 src/nexuscli/eval/real.py 白名单常量与对应注释、examples/evals/real/、
    tests 新增；不动 real 轨门链/审批/审计语义
  - verify: `uv run --extra dev python -m pytest -q -k "skill or eval"`
- [x] requirements 可用性过滤 + 文档同步与全量回归（bins/env 两类过滤进 registry，
  unavailable 不进候选、/skill list 标注；docs/skills.md 三层文档、docs/tools.md
  load_skill 段、README Skill 行）
  - depends-on: task_0002
  - boundary: 只改 src/nexuscli/skill/registry.py（过滤）、docs 两篇、README；tests 新增
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真实入口验收（场景 03 链路可达性，主会话经 spec_verify）
  - depends-on: task_0003
  - boundary: 只运行真实入口与读取报告，不产生代码改动
  - verify: `uv run nexuscli eval run --real --file examples/evals/real/03_skill_rule_follow.json; uv run python -c "import json,pathlib; r=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); runs=[x for x in r['scenarios'] if 'skill' in x['name']]; assert runs and runs[0]['runs'], runs; run=runs[0]['runs'][-1]; assert run.get('tool_calls') is not None or 'tool_sequence' in json.dumps(run), run.keys(); print('SKILL-TRACK-OK status=', run.get('status'), '| verdict recorded, compliance is a nightly signal')"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线；由 spec_verify 从主会话
      执行（AGENTS.md「执行环境与冒烟」守则）
    - 成功判定：末段断言通过打印 SKILL-TRACK-OK——链路可达且判定已记录；场景
      PASS/FAIL（模型是否遵循规则）是 nightly 行为信号，不构成本 verify 的通过条件
    - 失败处理：eval run 退出码 1 属预期可能（分号连接取末段退出码）；python 断言
      失败才算验证失败——先查报告是否更新、场景名是否含 skill、load_skill 是否真被
      调用（runs 里的工具序列）

---

**当前进度**：由脚本计算，无需手动维护
