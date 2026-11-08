# 贡献指南

谢谢考虑为 NexusCLI 出力。这篇文档写清本地开发循环：环境、测试、lint、类型检查与提交流程。能力速览见 [README.md](README.md)，运行教程见 [TUTORIAL.md](TUTORIAL.md)。

## 环境准备

前置要求：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)；可选 Node.js 22 LTS + npm（仅构建/测试 workbench 前端需要）。

```bash
git clone https://github.com/linixiyi/nexuscli.git
cd nexuscli
uv sync --extra dev --frozen
```

`uv sync --extra dev` 会创建 `.venv/`、按 `uv.lock` 安装精确依赖，并把本项目以可编辑方式装进去（含 pytest、ruff 等开发工具）。之后统一用 `uv run ...` 执行命令，不需要手动激活虚拟环境。可选 extras（`pdf` / `telemetry` / `telemetry-otlp`）按需追加，见 README「环境要求」。

## 本地开发循环

### 运行测试

```bash
uv run python -m pytest                        # 全量（CI 口径）
uv run pytest -q tests/test_tools.py           # 单文件
uv run pytest -q tests/test_tools.py -k name   # 按名过滤
```

写测试的两条硬规则（`tests/conftest.py` 已固化，新测试不要绕开）：

- conftest 的 autouse fixture 重定向 `HOME` **与** `USERPROFILE` 两个环境变量——任何测试都不得写真实用户目录（审计日志、会话、用量库会泄漏到 `~/.nexuscli`）。
- pty 类测试必须自建 pty 并应答光标位置查询（`ESC[6n`，CPR）；裸 pexpect 跑出来的「通过」是假结论。

### Lint 与格式

```bash
uv run ruff check .      # lint，与 CI 一致
uv run ruff format .     # 格式化；CI 只做 --check
```

### 类型检查

```bash
uv run --with mypy mypy src/nexuscli
```

mypy 按 `pyproject.toml` 的 `[tool.mypy]` 渐进接入配置运行：存量问题文件在 `ignore_errors` 排除清单里，清单「只减不增」——修掉一个文件就摘除一项，不得为容纳新错误而扩清单；新增模块必须干净。

### 前端（workbench）

```bash
cd frontend
npm ci
npm run build
npm run test:e2e         # 需要 Playwright Chromium；CI 里浏览器缺失会硬失败，本地可 skip
```

`frontend/dist` 已随仓库提交，改前端后记得重新构建——CI 会校验 dist 与源码是否同步。

### 文档联动

- `docs/runtime-api.md` 的端点表由 `scripts/generate_endpoint_docs.py` 从 `runtime/api.py` 生成，CI 跑 `uv run python scripts/check_endpoint_docs.py` 比对；改了 Runtime API 必须重新生成。
- README 只保留摘要与索引：先更新 `docs/` 对应专题文档，再核对 README 摘要句与索引行（分层口径见 README「文档分层说明」）。

## 提交 PR

1. 从 `main` 拉分支，PR 描述按 [PR 模板](.github/pull_request_template.md) 填写；报缺陷 / 提建议用 [issue 模板](.github/ISSUE_TEMPLATE)。
2. 推之前把上面的测试 / ruff / mypy 跑一遍——CI 跑的是同一组命令。
3. Commit message 用常规前缀（`feat:` / `fix:` / `docs:` / `test:` / `chore:`），一个 PR 聚焦一件事。
4. 改动触及对外行为（CLI 命令面、配置键、权限语义、工具行为）时，同步更新 README 摘要句与 `docs/` 对应文档。
5. **安全漏洞不要走公开 issue / PR**：按 [SECURITY.md](SECURITY.md) 的渠道私有报告。

提交即表示你同意贡献内容按仓库许可证（AGPL-3.0-only）授权，见 [LICENSE](LICENSE)。

面向 coding agent 的仓库守则（测试隔离、审批语义、可选依赖接入套路等）见 [AGENTS.md](AGENTS.md)。
