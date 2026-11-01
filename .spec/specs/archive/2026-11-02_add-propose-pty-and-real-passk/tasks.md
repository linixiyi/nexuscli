# propose pty 人审测试 + 沙箱守则合入 + 真实 pass^k 证据 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] `/skill propose` pty 交互人审测试（自建 pty + CPR 应答，y 落地与 s 跳余下两路径，
  断言 Evolved Rules 追加 / audit.jsonl reason="propose" / 提案记录落档；进程内假 LLM）
  - boundary: 只新增 tests/ 测试文件；conftest.py 仅限追加 fixture 且不改既有内容；
    不改 src 与既有测试
  - verify: `uv run --extra dev python -m pytest -q -k skill`
- [x] AGENTS.md 合入沙箱守则（spec §4.1 文本 verbatim，新节插在「审批与写域」之后）
  - depends-on: task_0001
  - boundary: 只改 AGENTS.md 单文件，新增一节，不动既有小节
  - verify: `rg -q "执行环境与冒烟" AGENTS.md && rg -q "禁止把沙箱网络失败" AGENTS.md && rg -q "2026-10-27_add-real-model-eval-track 增量提议" AGENTS.md`
- [x] 真实 pass^k k=2 证据（主会话执行，经 spec_verify）
  - depends-on: task_0002
  - boundary: 只运行真实入口与读取报告，不产生任何代码改动
  - verify: `uv run nexuscli eval run --real --repeat 2; uv run python -c "import json,pathlib; r=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); s=r['summary']; assert s['total_runs']==4 and s['scenarios_total']==2, s; assert all(x['pass_k']['repeat']==2 for x in r['scenarios']), [x['pass_k'] for x in r['scenarios']]; print('PASS_K_OK all_pass_rate=', s['all_pass_rate'])"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线、用户级 config 有效
      （2026-10-27 实测可用）；由 spec_verify 从主会话执行（沙箱连不了本地网关）
    - 成功判定：末段 python 断言通过并打印 `PASS_K_OK all_pass_rate=`（预期 0.5：
      场景 01 两次守卫 FAIL 是信号、场景 02 两次 PASS 含 judge 两次真实调用）
    - 失败处理：`eval run` 退出码 1 属预期（分号连接，判定只看末段断言）；python
      断言失败才算验证失败，检查报告是否真正更新（total_runs!=4 说明 run 没成）

---

**当前进度**：由脚本计算，无需手动维护
