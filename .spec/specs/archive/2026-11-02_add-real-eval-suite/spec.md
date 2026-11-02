# 真实轨道测评套件：30 场景 × 类别 × 难度 + manifest + 校准 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：为 real 评测轨道建一套自建数据集——30 个场景按 5 类（代码问答/仓库事实/
  诊断推理/技能遵循/信息抽取）× 3 档难度（short/medium/long），每类配 fixture 文件保证
  断言确定性，manifest 记录每题来源与入选理由（selection criteria 入档），真网关校准后
  回填观测结果（status/tokens/turns）。
- **目标用户**：nightly 真模型评测运营者；baseline 对比（后续包）的题库。
- **核心价值**：把「3 个冒烟场景」升级为可声明、可复现、有难度分布的垂类数据集——
  本仓垂类是「终端/CLI 里的仓库操作与代码理解」，对标 SWE-bench 之于通用 coding。

## 2. 假设与待确认

### 2.1 已确认事实
- real 轨 schema（docs/eval.md + loader strict）：name/prompt/assertions 七键子集/judge；
  目录加载按文件名排序、报错指名文件（loader.py:341-347）；`eval run --real --file <dir>` 可用
- REAL_TRACK_TOOLS 14 项只读白名单（含 load_skill，2026-11-02_add-skill-layered-loading）
  ——套件所有场景必须在该工具面内可解
- difficulty 三档已在 hermetic schema；real schema 的 assertions 复用 hermetic 七键校验
- 真网关 localhost:20128 可用（主会话）；AGENTS.md「执行环境与冒烟」：校准必须主会话跑，
  实现工作流只做创作+schema 校验
- 本仓 docstring/命令面为英文、文档为中文——套件 prompt 统一用**中文**（同时测指令跟随，
  与既有 01-03 英文冒烟场景区分开，manifest 里声明语言）
- judge 单次调用 ~2-5K tokens；单场景全 run ~10-25K；30 场景校准约 40-70 万 tokens（本地
  网关，一次性校准成本可接受）

### 2.2 关键假设
- 断言确定性靠三类锚点：仓库稳定事实（pyproject 项目名、README License 行等——作者必须
  亲自读文件核实后才能写进 final_contains）、自建 fixture（examples/evals/suite/fixtures/
  随仓库提交、内容冻结）、judge rubric（开放式题，阈值 0.6-0.7）
- 30 题 = 5 类 × 3 档 × 2 题；难度按预期工具调用数分（short ≤2、medium 3-4、long ≥5 或跨文件）
- prompt 中文、不含答案泄露（题面不得出现 final_contains 的原文片段）

### 2.3 待确认问题
- [x] 校准若个别场景 FAIL 怎么办？→ 结论：FAIL 是数据集的正常组成（难度分布需要失败
  样本），不改题——除非失败原因是题面歧义或断言错误（作者缺陷），那类修题并记录

### 2.4 可选解释与取舍
- 当前选择：fixture 随仓库提交而非运行时生成 -> 理由：确定性 + 可复现；内容冻结，改动
  走 spec
- 当前选择：不收录「需要写文件/跑命令」的题 -> 理由：real 轨只读白名单是安全边界（2026-10-27
  决策），套件不越界；写类场景的评测能力挂账（需 real 轨支持受控写，另行立项）
- 当前选择：manifest 用 JSON（机器可读，校准脚本可回填）而非 markdown 表

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 30 个场景 JSON（5 类 × 3 档 × 2）+ ≥8 个 fixture 文件 + manifest.json（id/category/
  difficulty/language/prompt 摘要/assertions 摘要/source 理由/校准回填字段）
- [x] schema 全量校验通过 + 场景名全局唯一 + prompt 无答案泄露的自检清单
- [x] 真网关校准：全量跑一遍，观测结果回填 manifest（status/tokens/turns），汇总
  通过率按类×难度

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 写类/执行类场景（需受控写，挂账）；baseline 对比 runner（后续包）；质检指标（后续包）
- hermetic 轨道改动

## 4. 最小实现路径
- 最简单可行方案：examples/evals/suite/ 目录树（5 个类别子目录 + fixtures/ + manifest.json），
  零 src 改动；校准回填用一次性脚本（task_0004 verify 内联）
- 暂不引入：数据集版本号、题目乱序/抽样机制、每题超时配置
- 不做的抽象/配置化：不做「数据集加载器」抽象（复用目录加载）；不做 manifest schema 校验器

## 5. 技术决策
- 技术栈：JSON 场景 + JSON manifest，零依赖零 src 改动
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：examples/evals/suite/**、tests 新增（schema 校验与 manifest 一致性测试）、
  docs/eval.md 套件一节、README 评测回归行
- 本轮不应触碰：src/**、examples/evals/real/ 既有 01-03、examples/evals/hermetic 场景
- Git integration branch：`spec/2026-11-02_add-real-eval-suite`

### 5.1 验证策略
- 范围级别：package
- 变更对象：examples/evals/suite/**、新增测试、docs/eval.md
- 快速检查：`uv run --extra dev python -m pytest -q -k eval`
- 集成检查：`uv run --extra dev python -m pytest -q`
- 全项目检查：适用外：纯数据集包零 src 改动；closeout 由发布门禁统一执行
- 升级触发：若发现必须改 src（如 loader）才能承载套件，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：task_0001 由主会话委托动态工作流——5 个作者子代理按类别并行创作（文件级
  所有权干净：各类别独立子目录），汇编子代理聚合 manifest，独立评审员审题；task_0004
  真网关校准按 AGENTS.md 守则由 spec_verify 在主会话执行

## 6. 成功标准与验证方式
- [x] 30 场景 schema 全过、名称唯一、目录加载可得 30 条 -> verify: task_0001
- [x] manifest 与场景文件一致（id 双向对齐、难度/类别字段匹配）-> verify: task_0002
- [x] 测试与文档就位，全量与 Ruff 双检全绿 -> verify: task_0003
- [x] 校准完成：30 题全部有观测结果回填 manifest，汇总表打印（按类×难度的通过率）
  -> verify: task_0004

## 7. 风险与约束
- 风险点：题面歧义或断言错锚导致校准批量 FAIL -> 缓解：评审员先审题（泄露/歧义/锚点
  核实），校准 FAIL 分诊（题缺陷修题记录 vs 真实难度保留）
- 风险点：仓库事实漂移让 final_contains 未来失真 -> 缓解：事实锚点优先用 fixture，
  仓库事实只选高稳定项（项目名/License 行），manifest 标注锚点类型
- 风险点：校准 token 消耗 -> 缓解：一次性 40-70 万 tokens 本地网关；manifest 回填后
  无需重复校准（除非改题）
