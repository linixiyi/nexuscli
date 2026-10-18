# 发布收口 0.2.0 与设计审计:site 重建、🟠 九断言、AGENTS.md 蒸馏、TUI 一致性、站点 polish - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁（自指命令在「最近一条」语义下不可满足）

## 阶段一:五条 lane 并行(第一轮工作流执行)
- [x] A1 发布文档与蒸馏:PRODUCT.md/README 数字与证据行刷新;AGENTS.md 从归档提议合成落地
  - boundary: PRODUCT.md、README.md、AGENTS.md(新建);产物证据写 .spec/specs/2026-10-19_release-020-design-audit/evidence/lane-docs.md
  - verify: manual: 主线程逐条核对数字与溯源(无机器可断言的文档事实)
- [x] B1 🟠 九断言:doctor、--version、/context、/tools、/policy、/usage、空 prompt Ctrl+C、Ctrl+D、bracketed paste
  - boundary: tests/ 新增测试文件(tests/test_doctor_surface.py、test_plain_exits.py、test_slash_surfaces_docs.py 等);若断言暴露真缺陷,缺陷本身记反馈不修(除非单行文案级)
  - verify: `.venv/bin/python -m pytest -q tests/test_doctor_surface.py tests/test_plain_exits.py tests/test_slash_surfaces_docs.py`
- [x] C1 平台与 CI:新增测试平台依赖审查(修或 skipif);CI 状态查询并如实记录
  - boundary: tests/ 既有 lane 测试文件的平台守卫行;evidence/lane-platform.md
  - verify: `.venv/bin/python -m pytest -q tests/` 中受影响文件
- [x] D1 TUI 一致性裁决与修复:边框 Table 按 pi 式无框方向改;新表面风格审查;渲染断言同步
  - boundary: src/nexuscli/entrypoints/commands/session_cmds.py、usage.py、repl.py 呈现层、render/;tests/test_render.py、test_repl.py 断言同步
  - verify: `.venv/bin/python -m pytest -q tests/test_render.py tests/test_repl.py tests/test_session_store.py tests/test_usage_history.py`
- [x] E1 站点 audit/polish:style.css 双主题对比度/a11y/响应式
  - boundary: site/style.css、site/build.py 模板段;evidence/lane-site.md
  - verify: `.venv/bin/python site/build.py`

## 阶段二:发版收尾(等 D1/E1 完成,主线程或第二轮执行)
- [x] F1 site 重建 + changelog + 版本号 0.2.0
  - boundary: site/ 产物、CHANGELOG.md(新)、pyproject.toml、src/nexuscli/__init__.py
  - verify: `.venv/bin/python site/build.py`
- [x] F2 全量质量门
  - boundary: 只跑验证,不改代码
  - verify: `.venv/bin/python -m pytest -q` 与 `.venv/bin/python -m ruff check src tests`

## 阶段三:验收
- [x] G1 矩阵 🟠 行与汇总计数按新断言更新;docs/testing-matrix.md 状态列如实
  - boundary: docs/testing-matrix.md
  - verify: manual: 主线程对照 lane 证据逐条改
- [x] G2 包 check 门禁 + done + push
  - boundary: .spec 包状态与 Git
  - verify: manual: check_spec_package.py 收敛 + push_spec_package.py 成功

---

**当前进度**：由脚本计算，无需手动维护
