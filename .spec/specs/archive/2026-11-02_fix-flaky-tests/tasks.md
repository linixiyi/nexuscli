# 全量 pytest 间歇失败：检测、根因修复与稳定性确认 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 检测扫描：4 轮全量（完整日志落 evidence/run-<n>.log）+ 嫌疑文件 5 轮压力循环，
  产出 evidence/flaky-findings.json（每例含测试名/轮次/traceback 全文/嫌疑机制分类）
  - boundary: 只写 .spec/specs/2026-11-02_fix-flaky-tests/evidence/**；不改 src/tests
  - verify: `uv run python -c "import json,pathlib; f=json.loads(pathlib.Path('.spec/specs/2026-11-02_fix-flaky-tests/evidence/flaky-findings.json').read_text()); assert f['full_runs']>=4 and f['full_runs_log_dir'] and isinstance(f['findings'],list), f.keys(); assert all(('test' in x and 'traceback' in x and 'mechanism' in x) for x in f['findings']); print('DETECTION-OK runs=', f['full_runs'], 'findings=', len(f['findings']))"`
    - 运行目录：仓库根；成功判定：打印 DETECTION-OK——4 轮以上完整日志与结构化
      findings 落档；findings 可为空列表（零命中是合法结果，触发 task_0002 的
      accepted_risk 分支）
    - 失败处理：日志目录缺失说明检测轮没跑全；json 断言失败按字段缺什么补什么
- [x] 逐例根因修复（每个 flaky 一修：测试缺陷修测试隔离/断言时序，产品缺陷修产品并
  解释；每例附「为什么以前会偶发」；零命中则写 accepted_risk 处置入 evidence）
  - depends-on: task_0001
  - boundary: 只改 tests/** 与（逐例说明的）src/**；不改场景数据/docs/README
  - verify: `uv run --extra dev python -m pytest -q tests/test_telemetry_otlp.py tests/test_skill_propose_pty.py tests/test_eval_suite.py tests/test_eval_baseline_ablation.py tests/test_eval_real_p3.py tests/test_skill_propose.py tests/test_mcp_oauth.py`
    - 运行目录：仓库根；成功判定：退出 0（嫌疑面全过）；零命中分支 verify 改为
      `verify: manual: 检测零命中，accepted_risk 处置已入档（evidence/flaky-findings.json）`
    - 失败处理：定位到具体文件后再收窄命令；新增修复点一并纳入
- [x] 稳定性确认：修复后连续 3 轮全量绿（逐轮结果入 evidence/stability-runs.json）
  - depends-on: task_0002
  - boundary: 只写 evidence/** 与（task_0002 已完成的）tests/src 改动；不新增代码
  - verify: `uv run python -c "import json,pathlib,subprocess; s=json.loads(pathlib.Path('.spec/specs/2026-11-02_fix-flaky-tests/evidence/stability-runs.json').read_text()); assert len(s['runs'])>=3 and all(r['exit']==0 for r in s['runs']), s; print('STABLE', len(s['runs']), 'consecutive green full runs')" && uv run --extra dev python -m pytest -q`
    - 运行目录：仓库根；前置：evidence/stability-runs.json 已由工作流检测阶段写入
      （3 轮各含 exit 与摘要行）；分号连接取末段退出码——末段是当场再跑一轮全量
      作为「最近一条」活证据
    - 成功判定：打印 STABLE 3 consecutive green full runs 且当场全量退出 0
    - 失败处理：历史 3 轮有非零 → 修复未达标，回 task_0002；当场轮失败 → 立即留存
      完整输出回 task_0002 重修

---

**当前进度**：由脚本计算，无需手动维护
