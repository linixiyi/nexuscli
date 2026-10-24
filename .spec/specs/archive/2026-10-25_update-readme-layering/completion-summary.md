# README 分层重排（F1） - 完成总结

## 交付结论
- 结果：完成
- 完成时间：2026-10-25 04:30

## 假设回顾
### 已验证假设
- 下沉的细节在 docs/ 有着落后，README 删除原文不构成信息丢失（映射表逐行人工核对兜底）
- `site/` 重建产物入库属仓库既有先例，可随本包提交

### 仍未完全验证的假设
- 无

## 交付范围
### 已交付
- 建 7 个新 docs 文件并完成既有 docs 吸收核对（03 映射表第 1 步）
- 登记 site/build.py 白名单（03 映射表第 2 步，与上一步同一提交）
- README 重排为三段式分层 + TUTORIAL.md 链接修补（03 映射表第 3 步）
- 落地 tests/test_docs_integrity.py 四用例（03「校验面」定稿）
- 全量回归（与 CI 一致的三步）
- 计划同步：勾选 master F1 + 写 .spec/autorun/ 摘要

### 未交付
- 无

### 偏差说明
- 无

## 简化决策
### 保持简单的关键选择
- 最简单可行方案：按 03 映射表移动正文到 7 个新 docs 文件 + 既有 docs 吸收核对 → 登记 build.py → 重写 README → 落地四用例完整性测试收口
- 暂不引入：文档链接检查的第三方工具（markdownlint 等）——自写四用例已覆盖锚点/相对链接/fragment/白名单四类断言
- 不做的抽象/配置化：不把映射表做成配置文件（一次性迁移，人工核对兜底）

### 本轮明确不做的内容
- F2-F13（工作台、执行可靠性等，属后续轮次，见 master）
- README 工作台章节（归 F8，第 4 轮）
- README 语义内容的增删（本轮只重排与下沉，不新增能力宣称）

## 变更边界
### 本轮主要改动模块
- 见 tasks.md 的 boundary 记录

### 明确未触碰的区域
- F2-F13（工作台、执行可靠性等，属后续轮次，见 master）
- README 工作台章节（归 F8，第 4 轮）
- README 语义内容的增删（本轮只重排与下沉，不新增能力宣称）

## 验证证据
### 备注
- 机器证据：runs.jsonl（见验证范围的机器验证率）

## 验证范围
- 级别：package
- 机器验证率：6/6
- 规则：check 与 done 复用该范围；只有记录升级触发时才执行全项目检查

## 哲学生效证据
### 行为成效回填
- Development Record 行为门禁通过

### 一致性门禁回顾
- 验收面（checklist 或成功标准）已通过

## Git 记录
- 日期时间：2026-10-25 04:30
- 范围：README 分层重排（F1）
- 功能：建 7 个新 docs 文件并完成既有 docs 吸收核对（03 映射表第 1 步）；登记 site/build.py 白名单（03 映射表第 2 步，与上一步同一提交）；README 重排为三段式分层 + TUTORIAL.md 链接修补（03 映射表第 3 步）；落地 tests/test_docs_integrity.py 四用例（03「校验面」定稿）；全量回归（与 CI 一致的三步）；计划同步：勾选 master F1 + 写 .spec/autorun/ 摘要
- 操作：提交 / 推送
- 成效：Development Record 行为门禁通过
- 提交：pending local commit
- 提交区间：338d46f4d9584f4ef715be16a0232ef59e208b36..604fda5109dcac056f7a2e0b2f41a99c24b267cf
- 推送：not-run (push stage follows commit)

## AGENTS.md 增量提议
```diff
- 新增 docs/*.md 必须同轮登记 site/build.py 的 DOC_META 与 PAGER_ORDER 两处白名单（键集合一致），否则 tests/test_docs_integrity.py 用例 4 的三集合断言红灯；README 行数验收带 360-440 与锚点/相对链接/fragment 完整性也由该文件机械把守——改文档先跑 uv run python -m pytest -q tests/test_docs_integrity.py。
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
