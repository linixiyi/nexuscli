# NexusCLI 审计遗留收口：安全、进程、渲染、平台 - 项目范围

<!-- spec-format: 2 -->

## 1. 问题定义
- **项目目标**：把 2026-10-18 三路真实使用审计中「发现了但上一轮没修」的 11 条挂账缺陷全部收口，并把测试矩阵里 30 项未测表面里的可自动化部分补上验证。
- **目标用户**：仓库作者（每天用）与按 README 上手的外部使用者（他们相信文档的每一句"支持"）。
- **核心价值**：从「功能存在」推进到「日常使用不踩坑、不撒谎、不丢数据」。

## 2. 假设与待确认

### 2.1 已确认事实
- 基线（2026-10-18，main@95f77f0）：`.venv/bin/python -m pytest` → 889 passed, 3 skipped；ruff check / format 全绿。
- 上一轮归档包：`.spec/specs/archive/2026-10-18_fix-real-usage-gaps/`，11 条已修 + 11 条挂账（见其 audit-report.md）。
- 挂账清单已逐条写进 `.spec/plans/README.md`（29 条 feature checkbox），是本包的唯一 scope 来源。
- 本机 GitHub 需经 `http://127.0.0.1:7897` 代理才能 push（直连报 SSL_ERROR_SYSCALL）；git 全局未配 proxy，按需 `-c http.proxy=` 传入。

### 2.2 关键假设
- 每条修复都能用「一条能真实跑出来的验证命令」证明，不靠"应该可以"。
- 进程组、连接池这类跨平台改动以 POSIX 为主路径，Windows 走降级分支并如实写边界。

### 2.3 待确认问题
- [x] 审计日志脱敏的边界：只截断正文与掩码敏感形态，不改审计字段结构 -> 结论：字段不变，正文超 500 字符截断，密钥形态掩码，文件 0600。
- [x] MCP 浏览器安全是否要保留 SSE：GET 返回 405 + 明确说明，不实现 SSE（本 server 无服务端推送需求）-> 结论：405。

### 2.4 可选解释与取舍
- 当前选择：按 A（安全审批）→ B（进程超时）→ C（渲染交互）→ D（平台文档）顺序推进，安全项先落地 -> 理由：安全缺陷的代价不可逆，其余可回滚。

## 3. 功能范围

### 3.1 核心功能（MVP）
- [x] A1 `-p` 接入审批回调 + `--yes` / `--no-approve` 显式语义
- [x] A2 审计日志脱敏（正文截断 + 密钥掩码 + 0600）
- [x] A3 MCP server 浏览器安全（Origin 校验 / 路径校验 / content-type 校验 / GET 405）
- [x] A4 Runtime API 任务接入审批通道
- [x] B1 进程组：`start_new_session` + `killpg`，`task_stop` 不再谎报
- [x] B2 bash 超时真正生效（孙进程持管道也按时返回）
- [x] B3 后台任务孤儿清理
- [x] C1-C11 渲染与交互 11 项
- [x] D1-D9 平台与文档 9 项

### 3.2 扩展功能
- 无

### 3.3 不在范围内
- 不重排 `src/nexuscli` 目录结构
- 不改事件流协议、审批四档语义
- 不引入 OS 级沙箱或新的外部服务依赖

## 4. 最小实现路径
- 最简单可行方案：按 `.spec/plans/README.md` 的 checkbox 逐条修，每条配回归测试；跨模块项（进程组、连接池）先写测试再改实现。
- 暂不引入：新的配置抽象层、插件化后端。
- 不做的抽象/配置化：不把审批策略做成可插拔后端；不把进程管理抽成通用库。

## 5. 技术决策
- 技术栈：Python 3.11+ / rich / prompt-toolkit / httpx / mcp（现状不变）
- 证据协议：evidence: runs（机器任务 verify 由 `scripts/spec_verify.py run` 执行并记录 runs.jsonl）
- 本轮允许改动：`src/nexuscli/`、`tests/`、`README.md`、`docs/*.md`、`.spec/`
- 本轮不应触碰：`uv.lock`、CI workflow、`site/` 生成产物（除非文档变更需要重建）
- Git integration branch：`spec/2026-10-18_fix-audit-residual`

### 5.1 验证策略
- 范围级别：package
- 变更对象：本任务包 boundary 涉及的模块、入口和数据契约
- 快速检查：`.venv/bin/python -m pytest -q <变更对应测试文件>`
- 集成检查：`.venv/bin/python -m pytest`（889 passed 基线不得回归）
- 全项目检查：仅在改动共享基础设施（进程管理、审批链、MCP 协议）时执行 `.venv/bin/python -m ruff check .` + `.venv/bin/python -m ruff format --check .`
- 升级触发：改动进程管理、审批链、MCP 协议或发布载荷时升级到项目级并记录理由

## 6. 成功标准与验证方式
- [x] A1-A4 安全项全部落地且有回归测试 -> verify: task_0004（实测：-p --yes 落盘、审计 0600、MCP 405/404/415/403、runtime 写被拒）
- [x] B1-B3 进程项全部落地且有回归测试 -> verify: task_0007（实测：group 停止后为空、bash 超时 2.06s 返回、atexit 清扫）
- [x] C1-C11 渲染交互项全部落地 -> verify: task_0018（pty 实测 48 列模式名保留、/sk<Tab> 补全、@image:、/clear 重绘、/config 摘要、/skill 未知报错、off 可见）
- [x] D1-D9 平台文档项全部落地 -> verify: task_0027（实测：MCP 复用同 pid 状态累计、缺参拒发、cron disabled/单行、端口可读、web_search 带原因）
- [x] 测试矩阵文档状态列同步更新 -> verify: task_0028（docs/testing-matrix.md 重写并接入站点）
- [x] 全量测试与 ruff 不回归 -> verify: task_0029（948 passed / 3 skipped；ruff check + format 全绿）

## 7. 风险与约束
- 风险点：进程组改造在 Windows 上语义不同 -> 缓解：POSIX 主路径 + Windows 降级分支，边界写进 docs。
- 风险点：审批回调接进 `-p` 会改变现有自动化行为 -> 缓解：默认仍是 deny，只有显式 `--yes` 或 `permissions.allow` 才放行，且写进 README。
- 风险点：MCP Origin 校验可能挡住合法本机客户端 -> 缓解：无 Origin 头（非浏览器）一律放行，只拒跨源浏览器请求。