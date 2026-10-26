# Mem0 式多信号记忆召回 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁
- 编排：实现走动态工作流（实现 → 评测 → 文档∥独立评审 → 有界修复 → 全量门），
  主会话验收收口

## 阶段一：实现

- [x] task_0001 信号层与融合（MemorySignal 接口 + 三路信号 + 时间分层 + schema 迁移）
  - boundary: 只改 src/nexuscli/memory/**（新建 signals.py；manager.py 融合/迁移/
    save 收窄）、src/nexuscli/rag/code_index.py 仅把 _bm25_score 提升为模块级公共
    bm25_score（行为不变，原处调用点同步）；搬运 /tmp/openviking-src
    openviking/retrieve/memory_lifecycle.py 至 memory/（保留上游版权头 + Ported-from
    注明 commit 9a57854）；不改去重/TTL/quota 语义与 search 返回格式
  - verify: `uv run python -m pytest tests -q -k memory`（前置：仓库根；成功判定：exit 0
    且既有 8 例 memory 测试不回归 + 新增单测覆盖：三路信号各自命中、top-K 对抗改善
    （纯词法排不进、多信号进前 K）、temporal_kind 迁移回填、意图加权、vector 缺席
    三路照常、hotness 边界、save 收窄不覆盖；失败处理：先查融合权重与排序键）
- [x] task_0002 工具面与注入面（save_memory 扩参 + 时态注入展示）
  - depends-on: task_0001
  - boundary: 只改 tools/builtins.py 的 save_memory/search_memory 工具 schema 与
    handler（entities/event_at/temporal_kind 可选参数透传）、prompt/assembler.py 最小
    适配（召回块带 temporal 标注，分块结构不变）、entrypoints/repl.py /save 参数面
  - verify: `uv run python -m pytest tests -q -k "memory_surface or recalled_memory"`（成功判定：
    exit 0 且新参数经工具 schema 校验、旧参数调用不回归；失败处理：查 schema 校验分支）
- [x] task_0003 评测场景与对抗验证
  - depends-on: task_0001
  - boundary: 只新增 examples/evals/ 记忆召回场景（编号顺延；脚本化假 LLM：先存多条
    干扰记忆再查询目标，断言用现有词汇）+ tests/test_memory_recall_eval.py（top-K
    命中率断言 + 「多信号 vs 纯词法」对抗对比断言，信号可注入隔离）
  - verify: `uv run python -m pytest tests -q -k memory_recall && uv run nexuscli eval run --file examples/evals --cwd /tmp/nexuscli-mem-eval`（前置：仓库根，/tmp 目录可重建；
    成功判定：两段 exit 0 且新记忆场景 PASS；失败处理：对抗用例失败先查 BM25 参数化
    与实体抽取的召回入口）
- [x] task_0004 文档联动
  - depends-on: task_0001
  - boundary: 只改 docs/memory-context.md（召回机制节：多信号/时态/ADD-only 收窄/
    可插拔向量位）与 README.md 记忆相关行（保持一句话风格）
  - verify: `uv run python -m pytest tests -q -k docs_integrity`（成功判定：exit 0；内容
    与实现一致性由 check 轮人工核对）

## 阶段二：集成验收

- [x] task_0005 全量门与真实入口冒烟
  - depends-on: task_0001, task_0002, task_0003, task_0004
  - boundary: 不改业务代码；只允许按门禁结果回修本包各任务 boundary 文件
  - verify: `uv run python -m pytest tests -q && uv run ruff check . && uv run ruff format --check . && rm -rf /tmp/nexuscli-mem-smoke && mkdir -p /tmp/nexuscli-mem-smoke/.nexuscli && cp ~/.nexuscli/config.json /tmp/nexuscli-mem-smoke/.nexuscli/config.json && chmod 600 /tmp/nexuscli-mem-smoke/.nexuscli/config.json && HOME=/tmp/nexuscli-mem-smoke USERPROFILE=/tmp/nexuscli-mem-smoke uv run nexuscli -p "记住这个项目用 uv 管理依赖" --yes && HOME=/tmp/nexuscli-mem-smoke USERPROFILE=/tmp/nexuscli-mem-smoke uv run nexuscli -p "这个项目用什么工具管理依赖？先搜索记忆再回答" --yes`（前置：仓库根且 ~/.nexuscli/config.json 存在（真实凭证经 cp 注入沙箱，
    密钥不进命令行）；成功判定：前三段 exit 0 且第二条 -p 输出含 uv（save 后真实
    recall 命中）；失败处理：冒烟失败先看 save_memory 审批放行与新信号在真实 LLM
    路径的表现）

---

**当前进度**：由脚本计算，无需手动维护
