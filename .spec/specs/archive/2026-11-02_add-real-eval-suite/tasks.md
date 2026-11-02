# 真实轨道测评套件：30 场景 × 类别 × 难度 + manifest + 校准 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 30 场景创作（5 类 × 3 档 × 2）+ fixtures + manifest 汇编（工作流并行创作 + 独立审题）
  - boundary: 只新增 examples/evals/suite/**（场景/fixture/manifest）与 tests/
    test_eval_suite.py；不改 src 与既有场景
  - verify: `uv run --extra dev python -m pytest -q tests/test_eval_suite.py`
- [x] 套件文档与全量回归（docs/eval.md 套件一节：定位/结构/manifest 字段/校准口径/
  语言声明；README 评测回归行补一句）
  - depends-on: task_0001
  - boundary: 只改 docs/eval.md 与 README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真网关校准（主会话经 spec_verify：全量 30 题跑一遍，回填 manifest，打印类×难度汇总）
  - depends-on: task_0002
  - boundary: 只运行真实入口与回填 manifest.json 的校准字段，不改场景题面与断言
  - verify: `uv run python -c "import json,pathlib; p=pathlib.Path('examples/evals/suite/meta/manifest.json'); man=json.loads(p.read_text()); rep=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); obs={s['name']:s for s in rep['scenarios']}; [e.update(calibration={'status':(o['runs'][-1]['status'] if o.get('runs') else 'fail'),'tokens':o.get('cost',{}).get('total_usage',{}).get('total_tokens',0),'turns':max([r.get('turns',0) for r in o.get('runs',[])],default=0)}) for e in man['scenarios'] for o in [obs.get(e['id'])] if o]; p.write_text(json.dumps(man,ensure_ascii=False,indent=2)+'\n'); missing=[e['id'] for e in man['scenarios'] if not e.get('calibration')]; assert not missing, missing; print('CALIBRATED', len(man['scenarios']), 'scenarios')"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线，且 30 题校准 run 已由主会话
      直接执行完毕（AGENTS.md「执行环境与冒烟」守则 + spec_verify 600 秒单命令上限——
      30 题串行真网关超出上限，故校准 run 本体在主会话后台执行、本 verify 只做回填与
      断言；首跑因 600s 超时失败一次即此因，属环境上限非实现缺陷）；eval run 退出码 1
      属预期可能（数据集含高难度失败样本）
    - 成功判定：打印 CALIBRATED 30 scenarios 与类×难度通过率网格；manifest 每题带
      calibration 字段；题面与断言零改动
    - 失败处理：obs 缺题说明场景没跑全（查目录加载与命名冲突）；回填脚本断言失败
      先核对报告场景名与 manifest id 是否一致
- [x] 校准分诊（FAIL 题逐条判：题缺陷修题并记录 vs 真实难度保留）
  - depends-on: task_0003
  - boundary: 只改 manifest 的校准备注字段与（如有题缺陷）对应场景文件；修题后需重跑
    该题校准回填
  - verify: `uv run python -c "import json,pathlib; man=json.loads(pathlib.Path('examples/evals/suite/meta/manifest.json').read_text()); bad=[e['id'] for e in man['scenarios'] if (e.get('calibration') or {}).get('status')!='pass' and not e.get('triage_note')]; assert not bad, bad; print('TRIAGE-CLEAN all failures annotated')"`
    - 运行目录：仓库根；成功判定：每个非 pass 题的 triage_note 字段写明
      「题缺陷已修（重校准）」或「真实难度保留」；全 pass 则网格即终态

---

**当前进度**：由脚本计算，无需手动维护
