# 整仓 MIT 转 AGPLv3 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：NexusCLI 整仓许可从 MIT 转为 AGPLv3，为后续直接复用 OpenViking
  （github.com/volcengine/OpenViking，AGPLv3）的 Python 源码扫清许可障碍。
- **目标用户**：仓库唯一版权持有人（全仓提交均本人，许可变更决定权完整）。
- **核心价值**：AGPLv3 代码可以合法合并 AGPLv3 代码；本轮完成许可面切换、依赖
  兼容性核查、OpenViking 只读勘察（可搬运清单）与 AGPL 义务文档化。

## 2. 假设与待确认

### 2.1 已确认事实
- 现许可面三处声明：LICENSE（MIT 全文）、pyproject.toml:7 `license = { text = "MIT" }`、
  README.md:9 徽章与 :383 License 一节；docs/*.md 零 MIT 提述
- src/tests/frontend/scripts 中的 MIT 字样 = 「Ported from kitagent (MIT, zed123214/kitagent)」
  出处注释（MIT 再分发的法定保留声明）+ "LIMIT/LIMITED" 字母巧合 + npm 依赖自身的
  license 字段（package-lock.json）——三类均不随本项目换许可而变
- site/*.html 是 docs/*.md 与 TUTORIAL.md 经 site/build.py 的构建产物，重新生成即同步
- AGPLv3 与 MIT/Apache/BSD 依赖单向兼容（宽松许可代码可纳入 AGPL 工程提供）

### 2.2 关键假设
- 用户为唯一版权持有人，无贡献者许可回流问题（如实际有外部 PR 贡献者，需另行取得
  其同意——git log 显示全部提交为本人，采信）
- SPDX 采用 AGPL-3.0-only（按「转 AGPLv3」字面）；若日后想改 AGPL-3.0-or-later，
  只动 pyproject 一行 + README 一句

### 2.3 待确认问题
- [x] SPDX 用 -only 还是 -or-later？→ 结论：-only（用户指令字面「转 AGPLv3」；
  与 OpenViking AGPLv3 合并无障碍）

### 2.4 可选解释与取舍
- 当前选择：kitagent 出处注释原样保留 -> MIT 许可的代码并入本项目后，其版权与许可
  声明必须随源码保留（MIT 条款要求）；换的是「本项目的对外许可」，不是抹掉上游出处。
  rg 门禁为此设排除模式而非追求字面清零

### 2.5 已证伪路径
- 无（暂无证伪记录；条目写：方案概述 + 证伪依据出处 + 日期）

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] 许可面替换：LICENSE 换 AGPLv3 全文（gnu.org 规范文本）；pyproject.toml
  license 字段；README 徽章与 License 一节；site/ 重新生成
- [x] 依赖许可证兼容性核查：Python 直接依赖逐个 + 间接依赖分类计数；frontend npm
  直接依赖逐个 + 间接分类计数；清单与结论写入包内 evidence
- [x] OpenViking 只读勘察：clone 到 /tmp/openviking-src，确认 AGPLv3，产出可搬运清单
  （对应本仓 memory 分层/目录级检索/skill 多信号检索三需求，逐项标
  直接可用/需适配/仅参考），写入包内 evidence；不搬运任何代码
- [x] AGPL 义务落文档：docs/workbench.md、docs/runtime-api.md、docs/mcp.md 三处
  部署边界补「网络使用须提供对应源码（AGPLv3 §13）」

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 任何 OpenViking 代码的实际搬运（后续包）
- CONTRIBUTING/提交者协议（DCO/CLA）引入
- 每个源码文件加许可证头（AGPLv3 不强制 per-file header，整仓以 LICENSE+pyproject
  声明为准；后续按需再加）

## 4. 最小实现路径
- 最简单可行方案：换三个声明面 + site 重生成 + 三处文档补义务 + 两个只读勘察
  （子代理并行）+ 机械门禁（rg 分类扫描/uv build/pytest）
- 暂不引入：NOTICE 文件、SPDX 头扫描工具（reuse tool）、贡献者协议
- 不做的抽象/配置化：不把许可检查做成 CI 步骤（本轮一次性核查，结论落 evidence）

## 5. 技术决策
- 技术栈：适用外（文档与许可文本为主）；勘察用只读子代理
- 证据协议：evidence: runs（机器任务 verify 命令由 scripts/spec_verify.py run 真实执行
  并记录 runs.jsonl；无命令任务写 verify: manual: <理由>；verify 命令不得自引用本包
  check 门禁）
- 本轮允许改动：LICENSE、pyproject.toml（license 字段）、README.md（徽章+License 节）、
  docs/{workbench,runtime-api,mcp}.md（部署边界节补 §13 说明）、site/**（build.py
  重新生成产物）、.spec/specs/2026-10-27_agpl-relicense/**
- 本轮不应触碰：src/**、tests/**、frontend/src/**、examples/**、scripts/** 的任何
  业务代码（出处注释原样保留）；CHANGELOG（历史记录不改写）
- Git integration branch：`spec/2026-10-27_agpl-relicense`

### 5.1 验证策略
- 范围级别：package
- 变更对象：许可声明面、三处文档、site 构建产物
- 快速检查：`uv run python -m pytest tests -q -k docs`
- 集成检查：`uv build`（许可元数据进构建产物即通过）+ rg 分类扫描（见 tasks）+
  `uv run --extra dev python -m pytest tests -q`（用户指定的全量测试，自愿加严执行，
  exit 0 已录 runs.jsonl task_0005）
- 全项目检查：适用外：本轮不涉及共享基础设施、跨模块契约或发布载荷（许可声明面与
  文档），全量测试已在集成检查自愿跑过；由发布门禁统一执行
- 升级触发：只有改动共享基础设施、跨模块契约、构建/依赖、发布载荷，或
  package/integration 检查暴露跨边界回归时，才把范围升级并记录理由

## 6. 成功标准与验证方式
- [x] 许可面替换完成且 rg 分类扫描零残留（出处注释/锁文件/归档/变更说明除外）
  -> verify: task_0001
- [x] 依赖许可证清单与兼容性结论落 evidence（Python + npm 全覆盖）-> verify: task_0002
- [x] OpenViking 勘察报告落 evidence（许可证确认 + 三需求可搬运清单三档标注）
  -> verify: task_0003
- [x] 三个对外面部署边界含 AGPLv3 §13 源码提供义务 -> verify: task_0004
- [x] uv build 通过 + 全量 pytest 绿 + site 与源文档一致 -> verify: task_0005

## 7. 风险与约束
- 风险点：rg 扫描误伤合法保留的出处注释 -> 缓解：门禁排除模式显式列出（kitagent
  出处/LIMIT 巧合/package-lock/归档/CHANGELOG），另加「出处注释计数不变」反向断言
- 风险点：OpenViking 仓库不存在/改名/许可证不符 -> 缓解：勘察子代理受阻即如实报告，
  不编造清单；许可证不符则升级为用户决策点
- 风险点：site/build.py 重新生成引入与 docs 无关的漂移 -> 缓解：重生成前后 git diff
  只允许许可相关行变化

### 5.4 编排策略
- route: explore
- immediate blocker: 许可面三处替换与 §13 文档（task_0001/0004）在主线程直接做——
  文件少、语义敏感，委托成本高于收益
- ownership: 两个只读勘察切片（OpenViking 勘察 task_0003、依赖审计 task_0002）交
  in-process 后台子代理，与主线程文件替换零文件交集
- waiting strategy: 勘察回包写入 evidence 后才过 task_0005 全量门
- verification gate: rg 分类扫描（声明面零残留 + 出处注释保真）+ uv build + 全量
  pytest + site 重生成 diff 审查
