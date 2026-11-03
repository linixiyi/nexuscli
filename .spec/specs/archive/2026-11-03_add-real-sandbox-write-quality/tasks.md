# real 轨受控写域沙箱 + ruff 质量指标 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一：实现
- [x] 受控写域沙箱 + 质量门全路径（schema 三键严格校验；场景级 registry/config 分支：
  追加 write_file/edit_file + PathGuard 钉沙箱 + 生命周期与清理；质量门纯函数
  ruff subprocess + py_compile + 降级；quality 块进报告；断言键 fail-closed；
  内置示例场景 04；全路径 hermetic 测试）
  - boundary: 只改 src/nexuscli/eval/loader.py、src/nexuscli/eval/real.py、
    examples/evals/real/（新增 04）、tests 新增；REAL_TRACK_TOOLS 主白名单与既有场景
    零改动；hermetic 轨零改动
  - verify: `uv run --extra dev python -m pytest -q -k eval`
- [x] 文档同步与全量回归（docs/eval.md：sandbox_write 语义、安全模型三层更新、quality
  块字段表、断言键、ruff 缺失降级；README 评测回归行）
  - depends-on: task_0001
  - boundary: 只改 docs/eval.md 与 README.md；不改 src 与 tests
  - verify: `uv run --extra dev python -m pytest -q && uv run --extra dev ruff check . && uv run --extra dev ruff format --check .`
- [x] 真网关冒烟（主会话经 spec_verify：场景 04 单跑）
  - depends-on: task_0002
  - boundary: 只运行真实入口与读取报告，不产生代码改动
  - verify: `uv run nexuscli eval run --real --file examples/evals/real/04_sandbox_write_quality.json; uv run python -c "import json,pathlib,glob; r=json.loads(pathlib.Path('.nexuscli/evals/last-real-report.json').read_text()); s=[x for x in r['scenarios'] if x['name'].startswith('real-sandbox')]; assert s and s[0]['runs'], s; q=s[0]['runs'][-1].get('quality'); assert q and isinstance(q.get('files_written'),list) and q['files_written'], s[0]['runs'][-1]; assert q.get('ruff_violations') is not None, q; residue=glob.glob(str(pathlib.Path.home()/'..')+'/../var/folders/**/nexuscli-eval-write-*', recursive=True); print('SANDBOX-OK files=', len(q['files_written']), 'ruff_violations=', q['ruff_violations'], 'compile_ok=', q.get('compile_ok'))"`
    - 运行目录：仓库根；前置：本地网关 127.0.0.1:20128 在线；主会话执行（守则）；
      eval run 退出码 1 属预期可能（质量断言可判 FAIL），分号取末段
    - 成功判定：打印 SANDBOX-OK——沙箱内真实写文件、ruff 违规数为数值（非 null）、
      quality 块落报告
    - 失败处理：quality 缺失看 loader/real 分支是否只认 sandbox_write 场景；ruff null
      说明 dev extra 未装（uv sync --extra dev 后重验）；沙箱残留用前缀 glob 手查并记录

---

**当前进度**：由脚本计算，无需手动维护
