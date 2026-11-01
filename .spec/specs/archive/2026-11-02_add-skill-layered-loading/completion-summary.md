# Skill L0/L1/L2 分层加载 + real 轨 load_skill 白名单修复 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-02 05:39

## 假设回顾
### 已验证假设
- load_skill 内容面只读（按名字在固定 skill 根解析，无路径参数、不可遍历），当初被
- load_skill 的 `level` 参数默认 full 保持向后兼容，现有用户与场景零破坏
- skill 规则遵循在真模型上高度可达但非 100%（real-readonly-converge 的 bash 教训）——

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- Skill 三层模型与调用点（registry 解析 abstract/overview + L1 自动生成兜底；
- real 轨修复与 skill 行为场景（load_skill 入 REAL_TRACK_TOOLS 白名单；白名单测试
- requirements 可用性过滤 + 文档同步与全量回归（bins/env 两类过滤进 registry，
- 真实入口验收（场景 03 链路可达性，主会话经 spec_verify）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：registry 解析扩字段 + 两个调用点（agent.py 候选行、builtins.py
- 暂不引入：skill 内容哈希失效检测、L1 缓存、多 skill 组合加载
- 不做的抽象/配置化：不做「层级策略」接口；不抽象通用 frontmatter 校验框架

### 本轮明确不做的内容
- XML 渐进披露、.abstract.md 旁挂文件、向量检索（recon 标仅参考）
- hermetic eval 的 skill 注入开关（保持 False）
- save_skill 写 abstract/overview 的表单化（文档说明手写即可）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- XML 渐进披露、.abstract.md 旁挂文件、向量检索（recon 标仅参考）
- hermetic eval 的 skill 注入开关（保持 False）
- save_skill 写 abstract/overview 的表单化（文档说明手写即可）

## 验证证据
### 备注
- 手工：工作流最终门曾报全量 pytest 退 1（评审修复轮次后，具体测试名未被捕获）；主会话复跑两次口径全绿（exit 0、无 FAILED），判为间歇性（与包 5 记录的既有竞态同谱系）。真实网关场景 03 首跑即 PASS：模型调用 load_skill 并遵循规则输出标记

## 验证范围
- 级别：package
- 机器验证率：4/4
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-11-02 05:39
- 范围：Skill L0/L1/L2 分层加载 + real 轨 load_skill 白名单修复
- 功能：Skill 三层模型与调用点（registry 解析 abstract/overview + L1 自动生成兜底；；real 轨修复与 skill 行为场景（load_skill 入 REAL_TRACK_TOOLS 白名单；白名单测试；requirements 可用性过滤 + 文档同步与全量回归（bins/env 两类过滤进 registry，；真实入口验收（场景 03 链路可达性，主会话经 spec_verify）
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：cf5343836ba894ceb6254822abef0aa608a82c68..cf5343836ba894ceb6254822abef0aa608a82c68
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
real 轨只读白名单增删必须重读工具的真实副作用面而非沿用历史分类：load_skill 因 record_use 计数器写 ~/.nexuscli/skills/usage.json 被当「用户态写」误伤排除，实际内容面只读、计数器是良性记账——加白名单时在注释里写明每项的副作用结论（来源：2026-11-02_add-skill-layered-loading REAL_TRACK_TOOLS 修订）
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
