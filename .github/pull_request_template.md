<!-- 标题格式建议：<类型>: <一句话>，如 fix: 修复 plan 模式下的假完成 -->

## 变更说明

<!-- 一段话说清这个 PR 做了什么；关联 issue 用 `Closes #N` -->

## 动机与背景

<!-- 为什么改：问题、现象，或 spec 任务块链接（.spec/specs/<spec-id>/tasks.md） -->

## 变更类型

- [ ] 新功能（不破坏既有行为）
- [ ] 缺陷修复
- [ ] 文档
- [ ] 重构（不改变行为）
- [ ] 测试 / CI
- [ ] 破坏性变更（影响与迁移方式写在文末）

## 自查清单

- [ ] `uv run ruff check .` 与 `uv run ruff format --check .` 通过
- [ ] `uv run python -m pytest` 通过，新增行为有对应测试
- [ ] `uv run python scripts/check_mypy_ratchet.py` 通过（与 CI 同口径；新增模块不在 `[tool.mypy]` 排除清单内）
- [ ] 改动触及对外行为时，README 摘要句与 `docs/` 对应文档已同步
- [ ] 未提交密钥、token、真实会话数据或 `.spec` 内部归档

## 破坏性变更

<!-- 无则写「无」 -->
