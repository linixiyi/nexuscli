# 第四波 安全评审 — 清单（只读评审）

你是安全评审员，**不修改任何文件**（发现交由修复工执行）。评审对象：`git diff`（工作区全部未提交改动 = 本轮六功能 + 未提交基线 F1）。逐项核查下列清单，每项给结论与证据（文件:行）：

## 威胁面清单（源自 spec §7 风险 + 平台约束）
1. **hooks 子进程执行**：默认未配置零执行；timeout 强制生效（不能被配置成无限）；PreToolUse 的 hook allow 不绕过 requires_approval（读 executor 接入代码验证控制流）；exit 2 阻断路径不可被 stdout JSON 伪造放行；stdin 载荷不含 api_key/凭据；shell=True 的命令来自 config（用户自有配置，接受，但确认没有从模型输出/工具结果流注入 hook 命令的路径）。
2. **!`cmd` 注入**：classify_command 高危拒绝路径真实生效（找 write 测试或亲自构造调用验证逻辑，读代码验证即可）；执行失败/超时不抛异常不泄漏 stderr 敏感内容；timeout=30 存在。
3. **子代理**：与主会话共享 executor/policy——审批回调透传后子代理内 requires_approval 工具仍会提示；深度防护不可绕过（subagent_depth 递增逻辑）；子代理系统提示不包含主会话 system_prompt 的提权内容；explore 白名单确实只含只读工具。
4. **权限规则**：deny 不可被 allow 遮蔽（优先级）；never 态 ask 兜底 deny（fail closed）；规则解析失败的条目跳过而非放行；`web_fetch(domain:...)` 匹配不遗漏子域与大小写。
5. **plan 模式**：非只读工具在 executor 层（不是提示词层）被拒；切换态恢复逻辑不会把 auto 态的 guard 关闭状态泄漏进 plan/default 态。
6. **审计与日志**：新增路径不记录明文凭据（AuditLog._redact 覆盖新字段）；hook stdin/stdout 与子代理报告进日志/审计前无敏感信息。
7. **平台安全约束（Mimosa）**：本轮新增代码若发起服务端请求仅 http/https 且校验 host 拒绝 localhost/环回/私有/保留地址（预期无新增请求路径——确认即可）；凭据经 env 间接引用；无拼接 SQL。
8. **回归红线**：`.spec/` 与 `docs/` 外不应有计划外文件；`git status` 无越界改动（对照各简报 boundary）。

## 输出格式（结构化返回，修复工照单执行）
对每个发现：`{where: "文件:行", what: "一句话问题", severity: "low|medium|high", suggestion: "最小修复建议", evidence: "代码摘录或命令输出"}`。没有发现的项也要在返回中列明 "已核查无发现"。**不要报告纯风格问题**（ruff 已门禁）；只报安全/正确性问题。
