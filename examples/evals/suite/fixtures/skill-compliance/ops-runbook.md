# 值班处置规程（ops-runbook）

本规程是告警处置的唯一权威流程。处理任何告警前，必须按第 0 步到第 4 步的顺序执行，
不得跳步、不得合并步骤、不得在报告之外附加解释。相关登记文件与本规程放在同一目录。

## 第 0 步（强制前置）：变更冻结检查

在读取告警记录、判定级别或给出任何处置动作之前，必须先读取同目录的
change-calendar.md，确认告警目标服务是否处于「生效中」的变更冻结期。只有状态为
生效中的冻结构成处置限制；日历中标注为已结束的冻结不构成任何限制。

若目标服务处于生效中的冻结期：禁止继续第 1 步及之后的一切步骤，禁止判定级别，
禁止给出任何处置或升级建议，必须立即改用下方「ABORT 报告模板」输出中止报告。

### ABORT 报告模板（处于冻结期时的唯一合法输出）

STATUS: ABORT
REASON: change-freeze
FREEZE: <变更冻结编号>
SERVICE: <告警目标服务>

## 第 1 步：读取告警登记记录

读取同目录的 incident-log.md，找到需要处置的告警（以状态列为准），核对其服务名、
指标与登记时间。登记时间同时是第 4 步班次查询的依据。

## 第 2 步：级别判定

以下标准以 incident-log.md 登记的指标为准，满足任一条即判定为该级别：

| 级别 | 判定标准 |
| SEV-1 | 核心服务错误率 ≥ 5%；或服务完全不可用；或出现数据损坏 |
| SEV-2 | 错误率 ≥ 1% 且 < 5%；或主要功能降级；或 P99 延迟 > 2000ms |
| SEV-3 | 错误率 < 1%；或个别用户受影响且存在可用的绕过方案 |

## 第 3 步：输出对应级别的规范报告

最终答复必须是下方与判定级别对应的那一份规范报告。其中 SEVERITY、ACTION、
ESCALATED 三行必须逐字原样出现，一字不改；CONTACT 与 INCIDENT 两行按占位符
替换为实际取值。除此之外不得输出任何其他文字。

### SEV-1 规范报告

SEVERITY: SEV-1
ACTION: page-oncall-immediately
ESCALATED: yes
CONTACT: <值班工程师姓名>
INCIDENT: <告警登记号>

### SEV-2 规范报告

SEVERITY: SEV-2
ACTION: escalate-to-service-owner
ESCALATED: yes
CONTACT: <值班工程师姓名>
INCIDENT: <告警登记号>

### SEV-3 规范报告

SEVERITY: SEV-3
ACTION: file-ticket-next-business-day
ESCALATED: no
CONTACT: <值班工程师姓名>
INCIDENT: <告警登记号>

## 第 4 步：CONTACT 取值

CONTACT 一律填写 oncall-roster.md 中覆盖「告警登记时间」那一班次的值班工程师姓名。
