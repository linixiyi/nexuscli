# 发布收口 0.2.0 与设计审计:site 重建、🟠 九断言、AGENTS.md 蒸馏、TUI 一致性、站点 polish - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**:把项目带到「可对外宣传」状态——文档站点与最新矩阵同步、CI/平台风险清零、文档数字与事实一致、三轮 spec 包的工程事实蒸馏进 AGENTS.md、9 个 🟠 表面补上断言、TUI 新旧表面风格一致、站点 polish,最后 changelog + 0.2.0 发版推送
- **目标用户**:clone 仓库的开源读者(站点/README 是第一印象)与日常使用 nexuscli 的开发者
- **核心价值**:对外呈现的事实全部真实且新鲜;设计语言一致;新用户看到的版本号/变更记录与实际功能面对得上

## 2. 假设与待确认

### 2.1 已确认事实
- 上一包(2026-10-18_verify-zero-and-yellow)已归档推送,矩阵 137✅/1🟡/9🟠/0⬜,1046 passed / 2 skipped
- site/docs/testing-matrix.html 落后于 docs/testing-matrix.md(site 产物入库,build.py 重建即可)
- `AGENTS.md` 不存在;三个归档包的 completion-summary 各有 AGENTS.md 增量提议未落地
- 版本 `0.1.0` 定义在 pyproject.toml 与 src/nexuscli/__init__.py;test_render.py 以参数传版本号,不硬断言
- PRODUCT.md(2026-10-18 第三轮 init)记录:主视觉表面=终端 TUI,pi 式观感方向,前景单色 ANSI 语义色、无 Panel/box 约束;边框 Table(/history /sessions /usage)与该约束的冲突未裁决
- 本机 gh 不可用、GitHub API 限流,CI 结果本地暂不可查

### 2.2 关键假设
- 9 个 🟠 表面补断言不需要改产品代码(真实运行已全通过);若发现真缺陷按缺陷处置
- 边框 Table 的裁决依据 PRODUCT.md 已确认的 pi 式方向:改为符合约束的呈现并同步渲染断言,而不是改约束

### 2.3 待确认问题
- 无(范围由用户 2026-10-19 直接给定)

### 2.4 可选解释与取舍
- 当前选择:发布收口与设计审计合成**一个包两个阶段** -> spec 不变量「一个会话同一时刻只拥有一个包/一条集成分支」,两包无法在同一个工作树并行;合并后 5 条 lane 仍可并行,发版收尾(站点重建/changelog/版本号)排在设计修复之后,0.2.0 一次带上全部
- 当前选择:CI 确认以「Windows 平台风险排查 + 如实记录查询结果」代替强等 CI -> 本机无 gh/无 token,查不到不造假;把可本地消除的风险(新测试平台依赖)消掉

## 3. 功能范围

### 3.1 核心功能(MVP)
- [x] Lane A 发布文档与蒸馏:PRODUCT.md/README 数字与证据行刷新;AGENTS.md 从三个归档包增量提议 + 本包新事实合成落地
- [x] Lane B 🟠 九断言:doctor、--version、/context、/tools、/policy、/usage、空 prompt Ctrl+C、Ctrl+D、bracketed paste 各补至少一条自动化断言
- [x] Lane C 平台与 CI:全部新增测试的 Windows/平台依赖审查(修或 skipif);CI 状态用一切可用手段查询并如实记录
- [x] Lane D TUI 一致性裁决与修复:边框 Table(/history /sessions /usage)按 pi 式无框方向裁决并改;新表面(/mcp 列表、审批原因行、快照确认文案、模型视觉徽标)风格审查;渲染字符串断言同步
- [x] Lane E 站点 audit/polish:site/style.css 双主题对比度/a11y/响应式 audit + polish
- [x] F 发版收尾(等 D/E 完):site build.py 重建、CHANGELOG 生成(generate_changelog.py)、版本号 0.2.0(pyproject + __init__)、全量门禁

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 14.2 远程图片复跑(上游环境依赖,挂账不变)
- 远程图片本地下载回退、召回约束产品级强保证(长期挂账)
- 新功能开发

## 4. 最小实现路径
- 最简单可行方案:5 条文件所有权互斥的 lane 并行 + 1 个串行收尾段;设计裁决依据 PRODUCT.md 既有方向,不重开产品讨论
- 暂不引入:新设计系统、站点框架更换、CI 新增矩阵
- 不做的抽象/配置化:不为 9 个断言新建测试框架;不动 site 构建架构

## 5. 技术决策
- 技术栈:pytest + ruff(check + format --check 双门,与 ci.yml 根目录口径一致)+ site/build.py;CHANGELOG 由 lane 手写草稿(spec skill 自带 generate_changelog.py 可选对照,仓库内无 scripts/)
- 证据协议:evidence: runs(机器任务 verify 命令由 scripts/spec_verify.py run 真实执行并记录 runs.jsonl;无命令任务写 `verify: manual: <理由>`;verify 命令不得自引用本包 check 门禁)
- 本轮允许改动:PRODUCT.md、README.md、AGENTS.md(新)、CHANGELOG.md(新)、pyproject.toml、src/nexuscli/__init__.py、tests/ 新增文件、entrypoints/render 呈现层、site/、docs/
- 本轮不应触碰:事件流协议、审批四档语义、工具行为语义;设计约束不可回退项(前景单色、ANSI 语义色)
- Git integration branch:`spec/2026-10-19_release-020-design-audit`

### 5.1 验证策略
- 范围级别:package
- 变更对象:文档/站点/呈现层/新测试文件/版本号
- 快速检查:`.venv/bin/python -m pytest -q tests/test_doctor_surface.py tests/test_plain_exits.py`(lane 内各自新文件)
- 集成检查:`.venv/bin/python -m pytest -q`(工作流尾段全量)
- 全项目检查:仅当改动触及共享基础设施或跨模块契约时升级;本包尾段与主线程收口均已全量跑 pytest + ruff(升级条件已满足并执行)
- 升级触发:设计修复若需要动渲染契约以外的共享模块,记入反馈由主线程裁决

## 6. 成功标准与验证方式
- [x] site 产物与 docs/ 一致(含最新矩阵) -> verify: `.venv/bin/python site/build.py` 后产物 mtime/内容比对
- [x] 9 个 🟠 表面各有至少一条自动化断言且全绿 -> verify: 新测试文件
- [x] 新增测试无未处置的平台依赖;CI 状态查询结果如实记录 -> verify: manual: lane 报告
- [x] PRODUCT.md/README 无过期数字与过期证据;AGENTS.md 存在且条目可溯源到归档提议 -> verify: manual: 主线程逐条核对
- [x] 边框 Table 裁决落地且渲染断言同步;新表面过风格审查 -> verify: `.venv/bin/python -m pytest -q tests/test_render.py tests/test_repl.py`
- [x] CHANGELOG 生成、版本号 0.2.0、全量 pytest + ruff 无回归 -> verify: `.venv/bin/python -m pytest -q` 与 `.venv/bin/python -m ruff check src tests`

## 7. 风险与约束
- 风险点:边框 Table 改呈现会动「渲染字符串是测试契约」的断言面 -> 缓解:lane D 同步更新断言并在证据里列出每处契约变更
- 风险点:版本号 0.2.0 影响 banner/doctor 输出的隐式断言 -> 缓解:收尾段全量 pytest 兜底
- 风险点:CI 在 Windows 上暴露新问题 -> 缓解:lane C 事前排查平台依赖;若 CI 后续红,按反馈开下一轮
