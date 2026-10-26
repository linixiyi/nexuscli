# 整仓 MIT 转 AGPLv3 - 任务拆解

## 使用规则
- 每个任务都要写清楚 `boundary` 和 `verify`；机器任务的 verify 以首个反引号 span 为执行的命令
- 如果一个任务没有验证方式，就不能开始；确无命令可执行的任务写 `verify: manual: <理由>`
- 发现的可执行问题必须回写本包，并在本轮做完
- verify 命令不得自引用本包 check 门禁

## 阶段一：许可面与文档

- [x] task_0001 许可面替换（LICENSE / pyproject / README / site）
  - boundary: 只改 LICENSE（AGPLv3 规范全文）、pyproject.toml 的 license 字段、
    README.md 徽章与 License 一节、site/**（经 site/build.py 重新生成）；不碰
    src/tests/frontend 业务代码与出处注释
  - verify: `! rg -n "License-MIT|text = \"MIT\"" pyproject.toml README.md docs/ && rg -q "GNU AFFERO GENERAL PUBLIC LICENSE" LICENSE && rg -q "Ported from kitagent \\(MIT" src tests scripts frontend/src`（前置：仓库根；成功判定：声明面零残留（第一段取反）+ LICENSE 为 AGPLv3 全文 + 出处注释仍存在；逐文件 42==42 保真对比在 check 轮以 git grep -c 完成（2026-10-26 已做：HEAD 42 = 工作树 42，见 evidence/review-notes.md）；失败处理：查残留行属声明面还是合法保留类
- [x] task_0002 依赖许可证兼容性核查
  - boundary: 只新增本包 evidence/dependency-licenses.md；不改任何依赖配置
  - verify: manual: 清单由只读子代理产出（PyPI/npm 元数据核查），无本仓命令可机器
    断言第三方元数据；验收口径 = 直接依赖全覆盖 + 分类计数 + 兼容性结论三要素齐全，
    由 check 轮人工核对
- [x] task_0003 OpenViking 只读勘察
  - boundary: 只新增本包 evidence/openviking-recon.md；clone 到 /tmp/openviking-src
    （仓库外）；不搬运任何代码
  - verify: manual: 勘察为只读调研，机器面 = clone 成功 + LICENSE 确认（记录在报告）；
    验收口径 = commit sha + 许可证识别依据 + 三需求清单（直接可用/需适配/仅参考三档）
    齐全，由 check 轮人工核对
- [x] task_0004 AGPL §13 义务落三个对外面文档
  - boundary: 只改 docs/workbench.md、docs/runtime-api.md、docs/mcp.md 的部署边界
    小节（各补一句网络使用源码提供义务）
  - verify: `rg -l "AGPLv3 §13" docs/workbench.md docs/runtime-api.md docs/mcp.md`（成功判定：
    三文件全部命中；失败处理：查小节标题是否为「部署边界」变体）
- [x] task_0005 全量门
  - depends-on: task_0001, task_0002, task_0003, task_0004
  - boundary: 不改业务代码；只允许按门禁结果回修本包 boundary 文件
  - verify: `uv build && uv run --extra dev python -m pytest tests -q && uv run --with markdown python site/build.py`（前置：仓库根；成功判定：三段全部 exit 0 且 site 重新生成后
    git diff 只含许可相关行；失败处理：按段定位，许可元数据问题查 pyproject，
    测试问题查 docs 断言）

---

**当前进度**：由脚本计算，无需手动维护
