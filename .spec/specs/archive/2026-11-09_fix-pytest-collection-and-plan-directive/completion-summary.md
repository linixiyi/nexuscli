# NexusCLI pytest 收集修复与 plan 指令收紧 - 完成总结

## 交付结论
- 结果：2 项任务通过门禁独立复跑与 spec_verify 亲跑（runs.jsonl 2/2 exit 0；0001 含全量 pytest ×2 无失败）；最终完成命令 5/5（ruff ×2、mypy 棘轮 UV_OFFLINE、全量 pytest ×2）；真实 --mode plan --yes 冒烟两次：只读闸保持（blocked/exit 1/无 COMPLETED），计划侧 bash 经 directive 反诱惑迭代消除；worker 中途偏离与 glob 噪声过滤挂账后续包（详见 5.5 修正日志）
- 完成时间：2026-11-09 02:40

## 假设回顾
### 已验证假设
- A1 | 空 `tests/__init__.py` 足以让裸 pytest（prepend 模式）将 tests/ 识别为包并使 `from tests.…` 可导入，无需改测试文件 | system | `python -m pytest` 与裸 pytest 的差异正是 cwd 是否入 sys.path + 包识别，主会话已复现 | CONFIRMED | if false: escalate 报告实际收集错误，不擅自改 6 个文件的 import
- A2 | directive 只改提示字符串，不触碰 0034 的 plan 只读闸与假完成修复 | system | 只读闸在任务执行层而非 directive；boundary 限定仅改常量 | CONFIRMED | if false: 回滚 directive 改动并记录偏离
- A3 | tests 包化（模块名 test_foo → tests.test_foo）不引入 basename 冲突 | system | tests/ 全部平铺无同名文件，全量验收覆盖 | UNVERIFIED | if false: 记录冲突并 escalate

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 0001 修复 tests 包导入收集（新增空 tests/__init__.py）
- 0002 plan 指令白名单收紧与文档同步

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：0001 先行（一个空文件 + 全量三条件验收），0002 随后（常量 + 文档 + 断言），最终门禁跑用户指定的全套完成命令。
- 暂不引入：pytest 配置变更（importmode 等）、测试文件拆分、plan 工具白名单的运行时强制（只读闸已存在）。
- 不做的抽象：不为 directive 建模板系统。
- B0 | literal ask | 空 `tests/__init__.py` + directive 常量收紧 + 文档/断言同步，两任务串行 | 用户指令的准确范围；机制前提已复现确认
- B1 | minimal change | 只做 0001 | pytest 收集修了但 plan 指令仍是宽口径，首批 bash 被拦的浪费仍在，不满足指令
- B2 | recommended alternative | 额外把白名单做成运行时强制闸（规划产物校验） | 更硬但越出指令范围且与 0034 只读闸重复

### 本轮明确不做的内容
- 不动五项已复测修复；不改 CI/billing 相关配置；不为 directive 引入配置化或新依赖。

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 不动五项已复测修复；不改 CI/billing 相关配置；不为 directive 引入配置化或新依赖。

## 验证证据
### 备注
- 脚本验证：`uv run pytest -q && uv run python -m pytest -q && uv run pytest -q tests/test_runtime_reads.py` -> exit 0 (runs:task_0001)
- 脚本验证：`uv run pytest -q tests/test_plan_readonly.py tests/test_headless_status.py tests/test_plan.py` -> exit 0 (runs:task_0002)

## 验证范围
- 级别：package
- 机器验证率：2/2
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## 失误方向统计
- DEFERENCE：0
- OVERREACH：0
- KNOWLEDGE：0
- PROCESS：1
  - spec.md ### 5.5 修正日志 round 1
- 只统计，不自动调整 Autonomy。

## Git 记录
- 日期时间：2026-11-09 02:40
- 范围：NexusCLI pytest 收集修复与 plan 指令收紧
- 功能：0001 修复 tests 包导入收集（新增空 tests/__init__.py）；0002 plan 指令白名单收紧与文档同步
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：dba3400942109ba5f57483deb207f3c68bb66869..d13267e28d1e484ad91632787654b5deada4c793
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
无

## 被拒绝的扩展提议
- 无

## 门禁证据
- 无

## 遗留事项
- 无；问题处置见下方结构化区块

## 问题处置

```json
{
  "issues": [],
  "version": 1
}
```
