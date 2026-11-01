# propose pty 人审测试 + 沙箱守则合入 + 真实 pass^k 证据 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：一次清掉三件挂账/待合入——①`/skill propose` 交互式人审补 pty 实测
  （此前只有非 TTY 单测，包 7 notCovered 明示）；②经用户批准，把「沙箱网络限制」
  增量提议合入 AGENTS.md；③`--repeat k>1` 的 pass^k 聚合补一次真实数据验证。
- **目标用户**：本仓开发者与维护者（测试覆盖面 + AGENTS.md 守则 + 真模型轨道证据）。
- **核心价值**：人审硬门有真实终端路径的测试背书；守则固化防止再次把沙箱网络失败
  误归因为环境配置；pass^k 语义有真实数据背书。

## 2. 假设与待确认

### 2.1 已确认事实
- propose.py 的人审（y 落地 / n 跳过 / s 跳余下）只有 pytest 单测（非 TTY stdin 门），
  无 pty 实测；AGENTS.md「测试与隔离」明示 pty 测试必须自建 pty 并应答光标位置查询
  `ESC[6n`（CPR），裸 pexpect 会整屏错位
- 仓库现唯一 openpty 先例：tests/test_plain_exits.py:65（裸 os.read 循环，未涉及 CPR——
  它测 --plain 退出，不进 prompt_toolkit 交互面）；假 LLM 注入模式遍布 tests/
  （ScriptedLlmClient、monkeypatch create_llm_client 等，见 test_cli_single_prompt.py、
  test_goal.py、test_eval_real_p3.py）
- 用户已批准合入沙箱守则增量提议（verbatim 文本见 §4）；包 7 的另一条提议（-p 不分发
  斜杠命令 → TTY 门）未经批准，不在本包
- 真模型轨道场景 01 预期 FAIL：模型试调 bash 被只读守卫拦截（2026-10-27 主会话实测
  信号，断言标记 + 审计留痕）——pass^k 真实验证的判定是**报告结构**而非全绿
- 主会话可连本地网关 localhost:20128（2026-10-27 实测真实调用成功）；spec_verify.py
  由主会话执行 verify 命令，因此 task_0003 的真实网关冒烟可机器验证
- 工作流/子代理沙箱连不了本地端口与外网 TLS（包 5/包 7 两次实证）——task_0003
  刻意不进实现工作流

### 2.2 关键假设
- pty 测试可在 pytest 内用进程内假 LLM 驱动 REPL 的 `/skill propose` 全流程（沿用仓库
  既有注入模式；注入点选最小缝——ProposalGenerator 构造参数或 create_llm_client
  monkeypatch，由实现者按命令层代码定）
- k=2 时场景 01 预计两次皆 FAIL（bash 尝试）、场景 02 两次 PASS（含 judge 两次真实
  调用）——all_pass_rate≈0.5 是预期值，不是回归
- prompt_toolkit 在 pty 测试环境可能不发 CPR 查询（取决于终端能力协商）；按 AGENTS.md
  规则实现应答逻辑，实测无查询则保留应答代码并记录

### 2.3 待确认问题
- [x] pty 测试若实测环境不发 CPR 查询怎么办？→ 结论：应答逻辑保留（防真实终端差异），
  测试以实际收到的输出断言为准，环境差异记录进 completion-summary

### 2.4 可选解释与取舍
- 当前选择：pass^k 验证判定报告结构（total_runs=4、repeat=2、断言后打印 all_pass_rate）
  而非全绿 -> 理由：场景 01 的守卫 FAIL 是 nightly 轨道要的真实信号，放宽断言反而丢信号
- 当前选择：AGENTS.md 新增「执行环境与冒烟」独立小节而非塞进既有节 -> 理由：主题是
  agent 执行环境不是 pytest 隔离
- 当前选择：task_0003 由 spec_verify 在主会话执行而不进实现工作流 -> 理由：本包合入的
  守则本身就要求真实网关冒烟走主会话

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] `/skill propose` pty 交互人审测试：自建 pty + CPR 应答，驱动 y 落地与 s 跳余下
  两条路径，断言 skill 文件 `## Evolved Rules` 追加、audit.jsonl 出现 reason="propose"
  行、提案记录落档
- [x] AGENTS.md 合入沙箱守则（verbatim 见 §4）
- [x] 真实 pass^k k=2 证据：`eval run --real --repeat 2` 后报告 total_runs=4、每场景
  pass_k.repeat=2、打印 all_pass_rate

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 修改场景 01 的 bash 断言（保持严格，FAIL 是信号）
- 包 7 其余 AGENTS.md 提议（-p TTY 门）——未经批准
- src/ 任何改动（pty 测试若发现必须改 src 才能注入，停下升级范围）

## 4. 最小实现路径
- 最简单可行方案：pty 测试单文件新增；AGENTS.md 节 verbatim 落地；real run 一次 k=2
- 暂不引入：通用 pty 测试基类抽象（第二个用例出现再说）；pass^k 的额外 CLI 断言命令
- 不做的抽象/配置化：不为 pty 测试做 prompt_toolkit mock 层

### 4.1 AGENTS.md 合入文本（verbatim，新节插在「审批与写域」节之后）

```markdown
## 执行环境与冒烟

- 工作流/子代理沙箱连不了本地端口与外网 TLS（2026-10-27_add-real-model-eval-track、
  2026-10-27_add-skill-evolve-proposal 两次实证：本地网关真模型冒烟在沙箱内退 1、
  沙箱内拉取 hatchling TLS 握手失败）。涉及真实 LLM、本地网关或外网的冒烟必须由
  主会话执行；子代理冒烟失败先查沙箱网络，再归因环境配置——禁止把沙箱网络失败
  写成「未配置 Key」。——来源:2026-10-27_add-real-model-eval-track 增量提议
```

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖（不引 pexpect）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：tests/ 新增（conftest.py 仅限追加 fixture，不改既有）、AGENTS.md 单文件
- 本轮不应触碰：src/**、docs/**、README、既有测试与既有 AGENTS.md 小节
- Git integration branch：`spec/2026-11-02_add-propose-pty-and-real-passk`

### 5.1 验证策略
- 范围级别：package
- 变更对象：tests 新增、AGENTS.md、.nexuscli/evals 运行产物（不入库）
- 快速检查：`uv run --extra dev python -m pytest -q -k skill`
- 集成检查：`uv run --extra dev python -m pytest -q`（conftest 追加影响全量，兜底）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷；closeout 由发布
  门禁统一执行
- 升级触发：若 pty 测试必须改 src 才能注入假 LLM，停下升级范围并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0，无强结构信号一律 local
- 执行说明：task_0001/0002 由主会话委托动态工作流的子代理实现（用户显式要求）；
  **task_0003 刻意不进工作流**——本包合入的守则即要求真实网关冒烟走主会话，由
  spec_verify 在主会话执行；主会话保留路由、验收、done/push 门禁

## 6. 成功标准与验证方式
- [x] pty 驱动 `/skill propose` 完成 y 落地与 s 跳余下两条路径，落地/审计/记录三处
  断言成立 -> verify: task_0001
- [x] AGENTS.md 新节含沙箱守则、来源注与「禁止把沙箱网络失败写成未配置 Key」表述 ->
  verify: task_0002
- [x] 真实 pass^k k=2：报告 total_runs=4、每场景 repeat=2、all_pass_rate 打印 ->
  verify: task_0003

## 7. 风险与约束
- 风险点：prompt_toolkit pty 测试脆弱（时序/CPR 协商差异） -> 缓解：按 AGENTS.md CPR
  规则实现 + 环境差异如实记录 + 不为过测放松断言
- 风险点：真实 run 消耗额度 -> 缓解：k=2 一次跑完（约 6-7 万 token），判定在报告结构
  不追求全绿重跑
- 风险点：pty 测试在实现者沙箱内行为与主会话不一致 -> 缓解：pty 是本地进程对不依赖
  网络；verify 由 spec_verify 在主会话复跑为准
