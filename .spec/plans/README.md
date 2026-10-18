# NexusCLI 收口计划（2026-10-18 审计遗留）

> 来源：`.spec/specs/2026-10-18_fix-real-usage-gaps/audit-report.md` 的「发现了但本轮没修的」11 条 + 三个 lane 报告里标 P1/P2 的其余条目。
> 判定口径：每条都要能说清「用户会怎么踩到」和「修完怎么证明」。

## 一、安全与审批（最高优先）

- [ ] A1 `-p` 单次模式接入审批回调，并提供显式 `--yes` / `--no-approve` 语义，让自动化场景不再等于没有 shell
- [ ] A2 审计日志脱敏：`write_file` 正文与 `bash` 命令按长度截断并掩码敏感形态，文件权限收到 0600
- [ ] A3 MCP server 浏览器安全：校验 Origin、拒绝非 MCP 路径与非 JSON content-type、补 SSE GET 或明确 405
- [ ] A4 Runtime API 任务接入审批通道（与 A1 同一套语义），消除「静默落盘 + 无审计」

## 二、进程与超时

- [ ] B1 `bash` / 后台任务改用进程组：`start_new_session=True` + `killpg`，`task_stop` 不再谎报 stopped
- [ ] B2 `bash` 超时真正生效：孙进程持有管道时也要按超时返回
- [ ] B3 后台任务孤儿清理：父进程退出后不留 `PPID=1` 的常驻进程

## 三、渲染与交互

- [ ] C1 窄终端状态栏降级：≤60 列时优先保留权限模式名，cwd 可截断
- [ ] C2 斜杠命令 Tab 补全：自定义 completer，`/sk` → `/skill`
- [ ] C3 placeholder 文案改为真实语法 `@image:`，并去掉不存在的 `@path/to/file`
- [ ] C4 `/compact` 压缩预算修正：压缩后 token 必须下降，否则如实报「无可压缩」
- [ ] C5 `/clear` 重绘 banner 并清零上下文条
- [ ] C6 `/config` `/policy` `/audit` 长 JSON 改为分页/摘要，不再刷屏
- [ ] C7 `/help` 增加一行说明与键位提示；`/skill <未知>` 不再静默列全表
- [ ] C8 审批块紧凑化：工具参数折叠为单行摘要，结果附行数
- [ ] C9 非 TTY 提示不硬折行，改走 stderr
- [ ] C10 `/effort off` 与「未设置」在状态栏可区分
- [ ] C11 `/model` 切到无密钥 provider 时报对变量名

## 四、平台与文档

- [ ] D1 MCP client 连接复用：同一 server 的长驻会话，避免每次调用新起进程
- [ ] D2 MCP 工具入参本地 schema 校验，缺必填参数直接报错
- [ ] D3 Runtime 同一 thread 并发 turn 串行化；不存在的 thread 返回 404
- [ ] D4 `cron next` 跳过 disabled 任务；`cron run` 输出可直接 pipe 给 `-p`
- [ ] D5 `web_search` 失败时给出真实原因，不再只回空 `Search error:`
- [ ] D6 `eval` 在 wheel 安装形态下带内置场景
- [ ] D7 端口占用等启动错误给出可读提示，不抛原始 traceback
- [ ] D8 `mcp list` 展示 `enabled:false`；`mcp.json` 损坏时报错而非静默当空配置
- [ ] D9 README 与实现对齐：自动快照默认关闭、`cron run` 用法、`docs/eval.md` 复制树说法

## 五、验证与文档

- [ ] E1 测试矩阵文档随修复更新（`docs/testing-matrix.md` 状态列）
- [ ] E2 全部修复补齐回归测试，全量 pytest + ruff 不回归
