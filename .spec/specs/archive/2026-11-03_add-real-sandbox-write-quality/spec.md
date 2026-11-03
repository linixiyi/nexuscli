# real 轨受控写域沙箱 + ruff 质量指标 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：给 real 轨加**可选的受控写域沙箱**——场景声明 `sandbox_write: true` 时，
  该场景允许 write_file/edit_file 写入一个临时沙箱目录（PathGuard 钉死沙箱根，bash 仍然
  禁止，审计照常）；场景结束后自动跑质量门（ruff check 沙箱内 .py + py_compile），指标
  进报告；场景可声明 `max_ruff_violations` / `require_compile_clean` 断言把质量变成
  PASS/FAIL 的一部分。这是「帖子第三部分质量类指标」的前置——有写才有可测的产物。
- **目标用户**：要评「agent 写的代码质量」的评测运营者（《阿里巴巴手册》打法的诚实版）。
- **核心价值**：real 轨从「只读理解」扩展到「受控产出」，产出质量可测、可断言、可对比
  （消融交叉的后置选项）；写域严格封闭在一次性沙箱内。

## 2. 假设与待确认

### 2.1 已确认事实
- real 轨现状零写副作用：REAL_TRACK_TOOLS 14 项只读（real.py:82-98），白名单外 deny；
  2026-10-27 决策「无人值守评测绝不允许写副作用」——本包是其**显式修订**（用户已拍板），
  写通道只在场景声明 sandbox_write 时开启且封闭沙箱
- 质量工具链：ruff 在 dev extra（`python -m ruff` 可用性随安装而异——需优雅降级）；
  py_compile 标准库恒可用；本仓已有「写 .py 自动诊断」的先例语义
- scenario schema 严格校验（loader），新键必须进白名单并写 track-conflict 规则
- last-real-report.json schema 此前零改动是其他包的边界；本包**有意**做增量扩展
  （新增 per-scenario quality 块，additive 不破坏既有键）
- PathGuard 既有机制（policy/path_guard）管写域；real.py 的 _load_real_config 构造配置链

### 2.2 关键假设
- 沙箱 = tempfile.mkdtemp（系统临时目录，前缀 nexuscli-eval-write-），write_file/edit_file
  的 PathGuard 根在该场景执行期间指向沙箱；仓库本体不可写
- bash/edit 类执行通道保持 deny——沙箱只开放「文件写」，不开放「命令执行」（执行 =
  真正的风险面，另案）
- ruff 不可用时 quality.ruff_violations = null；若场景声明了 max_ruff_violations，
  null 按 FAIL 处理（fail-closed，与 judge 判空同哲学）并给可操作指引
- 沙箱清理 best-effort：run 结束删除；失败一次性警告（进程内至多一次）

### 2.3 待确认问题
- [x] 写工具要不要加白名单常量？→ 结论：REAL_TRACK_TOOLS 主白名单不动；sandbox_write
  场景在构造该场景 registry/config 时**追加** write_file/edit_file（PathGuard 沙箱化），
  主白名单语义「默认只读」保持清晰

### 2.4 可选解释与取舍
- 当前选择：沙箱初始为空目录（不拷贝仓库）-> 理由：拷贝引入成本与泄密面；写任务的
  题面自包含（prompt 说明要求）
- 当前选择：质量门自动跑（sandbox_write 场景一律记录 quality 块），断言键只决定是否
  影响 PASS/FAIL -> 理由：指标常驻可对比，门禁可选
- 当前选择：bash 不随沙箱解禁 -> 理由：命令执行可写沙箱外、可联网，与「封闭写域」
  目标冲突；执行类场景另行立项

### 2.5 已证伪路径
- 无（暂无证伪记录）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 受控写域：schema 加 sandbox_write 键（严格校验）；开启时 write_file/edit_file
  进场景工具面且 PathGuard 钉沙箱（仓库本体不可写）；run 后清理沙箱（失败一次性警告）
- [x] 质量门：沙箱内 .py 自动 ruff check + py_compile，quality 块进报告（files_written/
  ruff_violations/compile_ok）；ruff 缺失降级 null；断言键 max_ruff_violations/
  require_compile_clean 影响 PASS/FAIL（fail-closed）
- [x] 测试全 hermetic：沙箱封闭性（仓库本体写被拒、沙箱内写成功）、bash 仍 deny、
  质量门（违规计数/编译失败/ruff 缺失降级/断言两侧）、清理、schema 校验、报告增量

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- bash/命令执行的沙箱解禁、网络访问控制、沙箱内 git 操作
- hermetic 轨、消融对比（--baseline 与 sandbox_write 组合挂账）
- 代码质量评分模型（只做客观指标：ruff 违规数 + 编译成败）

## 4. 最小实现路径
- 最简单可行方案：loader 加三键校验 → real.py 场景级 registry/config 分支（追加写工具 +
  PathGuard 沙箱化 + 沙箱生命周期）→ 质量门纯函数（收集 .py、ruff subprocess、py_compile）→
  报告 quality 块 → 内置示例场景 1 个
- 暂不引入：沙箱拷贝仓库、多沙箱、跨场景共享沙箱、质量评分加权
- 不做的抽象/配置化：不做「写域策略」接口；不做 ruff 配置定制（用默认规则集）

## 5. 技术决策
- 技术栈：Python 3.11+ / uv / pytest，零新依赖（ruff 走 dev extra，缺失降级）
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 `verify: manual: <理由>`；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：src/nexuscli/eval/loader.py、src/nexuscli/eval/real.py、
  src/nexuscli/entrypoints/cli.py（如需）、examples/evals/real/ 新增场景、tests 新增、
  docs/eval.md、README 评测回归行
- 本轮不应触碰：hermetic 轨、REAL_TRACK_TOOLS 主白名单语义、既有三文件 schema 既有键、
  agent 核心、policy 门链语义（只按场景配置实例化）
- Git integration branch：`spec/2026-11-03_add-real-sandbox-write-quality`

### 5.1 验证策略
- 范围级别：package
- 变更对象：eval loader/real、新场景、eval 测试、docs
- 快速检查：`uv run --extra dev python -m pytest -q -k eval`
- 集成检查：`uv run --extra dev python -m pytest -q`
- 全项目检查：适用外；closeout 由发布门禁统一执行
- 升级触发：若 PathGuard 现有语义不足以支撑「场景级写域重定向」，停下升级并记录理由

### 5.4 编排策略
- route: local
- 依据：route_decision.py = package-structure，score 0
- 执行说明：实现由主会话委托动态工作流子代理（用户显式要求）；真网关冒烟按守则由
  spec_verify 在主会话执行

## 6. 成功标准与验证方式
- [x] 受控写域全路径测试：沙箱封闭性（仓库写被拒/沙箱内成功）、bash 仍 deny、清理、
  schema 校验、既有只读场景零回归 -> verify: task_0001
- [x] 质量门全路径测试：违规计数/编译失败/ruff 缺失降级 null/断言两侧 fail-closed、
  quality 块进报告 -> verify: task_0001
- [x] 内置示例场景 04 + docs 同步 + 全量与 Ruff 双检全绿 -> verify: task_0002
- [x] 真网关冒烟：场景 04 跑通，quality 块落报告，沙箱已清理 -> verify: task_0003

## 7. 风险与约束
- 风险点：写逃逸（沙箱外落盘）-> 缓解：PathGuard 沙箱根 + 测试断言仓库本体写被拒 +
  审计照常；评审员专项检查
- 风险点：沙箱残留（清理失败）-> 缓解：best-effort 清理 + 一次性警告 + 沙箱目录前缀
  可识别（nexuscli-eval-write-）
- 风险点：ruff 缺失使质量断言假阴性 -> 缓解：null + fail-closed + 指引文案
