# 轮次摘要：2026-10-25_update-readme-layering（master F1）

## 交付
- README 743 行 → 361 行，三段式分层（TL;DR + 特性速览 17 行表 → 快速上手 → 核心概念 9 节 → 深入专题索引 20 篇 → 开发）
- 7 个新 docs 文件（config / repl-commands / tools / skills / memory-context / plan-execute / sessions），
  既有 docs 吸收核对（permissions 新增会话权限模式节、subagents 补 /fork //resume 邮箱句、8 个横幅改口径）
- site/build.py 白名单登记 7 条（DOC_META + PAGER_ORDER），站点 20 篇产出
- TUTORIAL.md 4 个失效锚点改指新结构
- tests/test_docs_integrity.py 四用例（锚点/相对链接/fragment/行数+白名单三集合）全绿

## 证据
- runs.jsonl：task_0001-0005 机器验证（site 构建 smoke、ruff、行数断言、四用例、全量回归 1062 passed）
- 提交：cad87e7（第 1-2 步）、4e5da25（第 3 步）、604fda5（第 4 步）

## 简化决策（对照 spec §4 暂不引入清单）
- 未引入第三方链接检查工具（自写四用例覆盖）；未做映射表配置化（一次性迁移人工核对）
