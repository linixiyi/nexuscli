# propose pty 人审测试 + 沙箱守则合入 + 真实 pass^k 证据 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-02 02:04

## 假设回顾
### 已验证假设
- pty 测试可在 pytest 内用进程内假 LLM 驱动 REPL 的 `/skill propose` 全流程（沿用仓库
- k=2 时场景 01 预计两次皆 FAIL（bash 尝试）、场景 02 两次 PASS（含 judge 两次真实
- prompt_toolkit 在 pty 测试环境可能不发 CPR 查询（取决于终端能力协商）；按 AGENTS.md

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- `/skill propose` pty 交互人审测试（自建 pty + CPR 应答，y 落地与 s 跳余下两路径，
- AGENTS.md 合入沙箱守则（spec §4.1 文本 verbatim，新节插在「审批与写域」之后）
- 真实 pass^k k=2 证据（主会话执行，经 spec_verify）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：pty 测试单文件新增；AGENTS.md 节 verbatim 落地；real run 一次 k=2
- 暂不引入：通用 pty 测试基类抽象（第二个用例出现再说）；pass^k 的额外 CLI 断言命令
- 不做的抽象/配置化：不为 pty 测试做 prompt_toolkit mock 层

### 本轮明确不做的内容
- 修改场景 01 的 bash 断言（保持严格，FAIL 是信号）
- 包 7 其余 AGENTS.md 提议（-p TTY 门）——未经批准
- src/ 任何改动（pty 测试若发现必须改 src 才能注入，停下升级范围）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 修改场景 01 的 bash 断言（保持严格，FAIL 是信号）
- 包 7 其余 AGENTS.md 提议（-p TTY 门）——未经批准
- src/ 任何改动（pty 测试若发现必须改 src 才能注入，停下升级范围）

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：3/3
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-11-02 02:04
- 范围：propose pty 人审测试 + 沙箱守则合入 + 真实 pass^k 证据
- 功能：`/skill propose` pty 交互人审测试（自建 pty + CPR 应答，y 落地与 s 跳余下两路径，；AGENTS.md 合入沙箱守则（spec §4.1 文本 verbatim，新节插在「审批与写域」之后）；真实 pass^k k=2 证据（主会话执行，经 spec_verify）
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：cf5343836ba894ceb6254822abef0aa608a82c68..cf5343836ba894ceb6254822abef0aa608a82c68
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
沙箱 shell 默认 TERM=dumb 会让 prompt_toolkit 的 Vt100_Output.responds_to_cpr 直接跳过 CPR 查询——pty 测试必须显式钉 TERM=xterm-256color，否则按 AGENTS.md 实现的 CPR 应答代码根本不会被触发，测了个寂寞（来源：2026-11-02_add-propose-pty-and-real-passk 实测观察，cpr_answered=2）
```

## 被拒绝的扩展提议
- 无

## 门禁证据
- check_spec_package.py exit 0

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [],
  "version": 1
}
```
