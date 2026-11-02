# 真实轨道测评套件：30 场景 × 类别 × 难度 + manifest + 校准 - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-11-02 11:19

## 假设回顾
### 已验证假设
- 断言确定性靠三类锚点：仓库稳定事实（pyproject 项目名、README License 行等——作者必须
- 30 题 = 5 类 × 3 档 × 2 题；难度按预期工具调用数分（short ≤2、medium 3-4、long ≥5 或跨文件）
- prompt 中文、不含答案泄露（题面不得出现 final_contains 的原文片段）

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 30 场景创作（5 类 × 3 档 × 2）+ fixtures + manifest 汇编（工作流并行创作 + 独立审题）
- 套件文档与全量回归（docs/eval.md 套件一节：定位/结构/manifest 字段/校准口径/
- 真网关校准（主会话经 spec_verify：全量 30 题跑一遍，回填 manifest，打印类×难度汇总）
- 校准分诊（FAIL 题逐条判：题缺陷修题并记录 vs 真实难度保留）

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：examples/evals/suite/ 目录树（5 个类别子目录 + fixtures/ + manifest.json），
- 暂不引入：数据集版本号、题目乱序/抽样机制、每题超时配置
- 不做的抽象/配置化：不做「数据集加载器」抽象（复用目录加载）；不做 manifest schema 校验器

### 本轮明确不做的内容
- 写类/执行类场景（需受控写，挂账）；baseline 对比 runner（后续包）；质检指标（后续包）
- hermetic 轨道改动

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- 写类/执行类场景（需受控写，挂账）；baseline 对比 runner（后续包）；质检指标（后续包）
- hermetic 轨道改动

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

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
- 日期时间：2026-11-02 11:19
- 范围：真实轨道测评套件：30 场景 × 类别 × 难度 + manifest + 校准
- 功能：30 场景创作（5 类 × 3 档 × 2）+ fixtures + manifest 汇编（工作流并行创作 + 独立审题）；套件文档与全量回归（docs/eval.md 套件一节：定位/结构/manifest 字段/校准口径/；真网关校准（主会话经 spec_verify：全量 30 题跑一遍，回填 manifest，打印类×难度汇总）；校准分诊（FAIL 题逐条判：题缺陷修题并记录 vs 真实难度保留）
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：f97b4a516080a332f44536730b8bcec99bc87f8f..f97b4a516080a332f44536730b8bcec99bc87f8f
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
eval 数据集的辅助 JSON 必须放目录内子目录：examples/evals/ 顶层是 hermetic 轨 builtin_eval_dir 加载根、suite/ 顶层是 real 轨 --file 加载根，两层 glob 都非递归——manifest 放任何一层顶层都会被当场景吃掉报 unknown keys（用 meta/ 子目录免疫）；另：回填脚本重建整字典会抹掉同字典内的人工注记——机器数据与人工分诊拆独立字段（calibration vs triage_note）互不覆盖（来源：2026-11-02_add-real-eval-suite 两次门禁失败复盘）
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
