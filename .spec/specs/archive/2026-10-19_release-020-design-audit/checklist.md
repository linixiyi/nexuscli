# 发布收口 0.2.0 与设计审计 - 验收清单

- [x] A1 PRODUCT.md/README 数字与证据行与仓库事实一致;AGENTS.md 存在且条目可溯源
  - 证据:evidence/lane-docs.md + 主线程逐条核对
- [x] B1 九个 🟠 表面各有自动化断言且全绿
  - 证据:`.venv/bin/python -m pytest -q tests/test_doctor_surface.py tests/test_plain_exits.py tests/test_slash_surfaces_docs.py`
- [x] C1 新增测试平台依赖清零(修或 skipif);CI 查询结果如实记录
  - 证据:evidence/lane-platform.md
- [x] D1 边框 Table 裁决落地、新表面过风格审查、渲染断言同步
  - 证据:`.venv/bin/python -m pytest -q tests/test_render.py tests/test_repl.py tests/test_session_store.py tests/test_usage_history.py` + evidence/lane-tui.md
- [x] E1 站点 audit 完成,style.css polish 落地
  - 证据:evidence/lane-site.md + `.venv/bin/python site/build.py`
- [x] F1 site 产物与 docs/ 一致;CHANGELOG 生成;版本号 0.2.0
  - 证据:`.venv/bin/python site/build.py` + CHANGELOG.md + pyproject/__init__ 版本串
- [x] F2 全量 pytest + ruff 无回归
  - 证据:`.venv/bin/python -m pytest -q` 与 `.venv/bin/python -m ruff check src tests`
- [x] G1 矩阵 🟠 行与汇总计数更新
  - 证据:docs/testing-matrix.md diff
- [x] G2 check 收敛 + done + push
  - 证据:check_spec_package.py 收敛输出 + push 成功

## 处置记录

(问题 → 处置方式:resolved_current / resolved_followup / accepted_risk / external_blocked / non_actionable)

## 处置记录(第二轮 6 条反馈,均独立复核)

1. /audit 4 列断言第一轮实际不存在 → non_actionable(actionable=false):lane 已补 test_audit_with_records_is_a_frameless_four_column_summary 契约
2. 两处标题串 pin 在权限外测试文件 → non_actionable(actionable=false):实现保留同串故绿;后续改措辞需同步(test_slash_surfaces_docs.py:80、test_integration_platform.py:271)
3. test_usage_history.py 的 modified 来自先前轮次 → non_actionable(actionable=false):本包未触碰
4. .brand-logo 渐变白字 ≈1.8:1 → accepted_risk:WCAG 1.4.3 logo 豁免,品牌 logotype;复用该渐变做正文组件时须套用本轮压深 stop
5. markdown 未声明为项目依赖 → accepted_risk:canonical 构建命令已统一为 build.py:3 头注 uv run --with markdown,pygments;加 dev extra 涉及 uv.lock 同步,收益边际
6. .venv 手装 markdown 之谜 → non_actionable(actionable=false):两时点各自属实(第一轮缺、两轮间被手装 3.11.1);canonical 命令已不依赖 .venv 状态

## 验收证据

- 外部对标:与 CI 口径对齐(ruff check . / ruff format --check . 从仓库根)
- 脚本验证:`.venv/bin/python -m pytest -q`(第二轮尾段 exit=0);`.venv/bin/python -m ruff check .` 与 `.venv/bin/python -m ruff format --check .`(均 exit=0,CI 同口径);`uv run --with markdown,pygments python site/build.py`(exit=0);主线程修复后全量 1055 passed / 2 skipped(2026-10-19)
- 旧新对比:矩阵 137✅/9🟠/1🟡 → 146✅/0🟠/1🟡;版本 0.1.0 → 0.2.0;AGENTS.md 从无到有;CI lint 红灯(归档 pty_driver 6 违规)清零
- 差异边界:Windows CI 实证待远端第一次绿 lint 运行(本机无凭据);14.2 复跑挂账上游
- 行为成效:站点双主题对比度达标(.btn-primary 压深)、TUI 八个表面全部无框化、九个 🟠 表面断言补齐、AGENTS.md 三轮蒸馏落地
- 验证范围:package

**验收结果**：通过
