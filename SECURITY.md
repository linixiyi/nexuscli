# 安全策略

## 支持版本

| 版本 | 支持状态 |
|---|---|
| 0.2.x | ✅ 接收安全修复 |
| < 0.2 | ❌ 不再维护，请升级 |

## 报告漏洞

**不要用公开 issue / PR / discussion 报告安全漏洞。**

请使用 GitHub 私有漏洞报告：仓库页 **Security → Advisories → Report a vulnerability**（直达 [security/advisories/new](https://github.com/linixiyi/nexuscli/security/advisories/new)）。该渠道只有维护者可见，支持私下讨论与修复协调。

报告里尽量带上：

- 受影响的版本（`nexuscli --version`）与安装方式（uv tool / pip / 源码）；
- 复现步骤或最小 PoC；
- 影响面评估：能读什么、能写什么、是否外带数据/凭据；
- 是否需要 CVE 编号。

维护者通常在几天内首次回复；修复发布前请勿公开披露细节。

## 关注面（什么样的报告最有价值）

- 路径守卫与命令守卫绕过：写域外文件、命令放行清单（`allow` 规则）越权命中；
- 环境清洗缺口：密钥形态变量经 bash / 后台任务子进程外带；
- Runtime API / workbench 鉴权：x-api-key 之外的未授权访问、bootstrap 口令 / CSRF / Origin 白名单链的绕过；
- MCP server 暴露面：`NEXUSCLI_MCP_API_KEY` 凭证门缺失或错配；
- 审计日志完整性：该记的被静默跳过、记录可被伪造或篡改。

## 明确的边界：护栏不是沙箱

NexusCLI 的安全机制是**给可信用户运行可信配置的护栏，不是 OS 级沙箱**。如实边界如下：

- 权限规则（deny > ask > allow）、HITL 审批、plan 只读闸、审计日志都工作在**用户自身权限**之上，不隔离文件系统与网络；
- `bash`、后台任务派生与 slash 命令注入（经 ToolExecutor 同一门链执行）会把清洗后的环境传给子进程（剔除密钥形态变量），但 **MCP stdio server 与 hooks runner 未清洗**；
- `-p` 单次模式没有审批弹窗，需要审批的工具按失败关闭——`--yes` 是显式放行，等价于信任整条 prompt；
- `mcp serve` 在协议层强制 `hitl_mode = "never"`：接入该 server 的任何客户端都获得**无需审批的完整工具能力**（包括执行命令、读写文件），只把 server 暴露给可信客户端；
- workbench 与 Runtime API 默认只绑 127.0.0.1、无多用户认证，不要开放公网。

因此：不要把 Agent 指向不可信内容后开 auto/full-access 模式；不要用 NexusCLI 替代容器/虚拟机隔离运行不可信代码。OS 级沙箱（macOS seatbelt、Linux bubblewrap/Landlock）在路线图上，落地前请用外部机制兜底。

## 升级与披露

- 修复以 patch 版本发布并写入 [CHANGELOG.md](CHANGELOG.md)；
- 采用协同披露：修复发布后再公开细节；披露说明中署名（或匿名）由报告者选择。
