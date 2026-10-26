# NexusCLI 依赖许可证清单（AGPLv3 迁移审计，2026-10-26）

审计者：只读子代理（fresh context）；方法 = `uv export --format requirements-txt`
解析 41 个 Python 包（含 dev/extra），许可证取自本地已安装元数据
（importlib.metadata，License-Expression → License → Classifier 逐级回落），
colorama/pywin32 因平台 marker 未装本机改从 PyPI JSON API 核实；npm 用
`npm ls --depth=0` + 批量读 node_modules/<pkg>/package.json 的 license 字段。

## 一、Python 直接依赖（pyproject.toml，11 个）

| 包 | 版本 | 许可证 | 风险 |
|---|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause | 无 |
| mcp | 1.28.1 | MIT | 无 |
| pillow | 12.3.0 | MIT-CMU（HPND+MIT 宽松） | 无 |
| prompt-toolkit | 3.0.52 | BSD-3-Clause | 无 |
| rich | 15.0.0 | MIT | 无 |
| typer | 0.26.8 | MIT | 无 |
| pypdf（pdf/dev extra） | 6.19.0 | BSD-3-Clause | 无 |
| opentelemetry-api（telemetry/dev） | 1.45.0 | Apache-2.0 | 无 |
| opentelemetry-sdk（telemetry/dev） | 1.45.0 | Apache-2.0 | 无 |
| pytest（dev） | 9.1.1 | MIT | 无 |
| ruff（dev） | 0.15.20 | MIT | 无 |

## 二、Python 间接依赖（30 个，lock 解析）

- MIT × 16：anyio、pydantic、pydantic-core、pydantic-settings、h11、httpx-sse、
  idna、jsonschema、jsonschema-specifications、annotated-types、annotated-doc、
  markdown-it-py、mdurl、python-dotenv、click、sniffio 等
- BSD-3-Clause × 6：starlette、sse-starlette、uvicorn、httpcore（metadata 仅写
  通用 "BSD License"，上游 LICENSE 为 3-clause）等
- 逐个列出的非 MIT/BSD：

| 包 | 版本 | 许可证 | 风险 |
|---|---|---|---|
| certifi | 2026.6.17 | MPL-2.0 | 弱互斥（文件级），与 AGPL 兼容，无风险 |
| cffi | 2.1.0 | MIT-0 | 无 |
| cryptography | 49.0.0 | Apache-2.0 OR BSD-3-Clause | 无 |
| pygments | 2.20.0 | BSD-2-Clause | 无 |
| python-multipart | 0.0.32 | Apache-2.0 | 无 |
| shellingham | 1.5.4 | ISC | 无 |
| typing-extensions | 4.16.0 | PSF-2.0 | 无 |
| colorama（win marker） | 0.4.6 | BSD-3（PyPI 核实） | 无 |
| pywin32（win marker） | 312 | PSF-2.0（PyPI 核实） | 无 |

## 三、frontend npm 依赖（frontend/package.json）

| 包 | 版本 | 许可证 | 分发属性 |
|---|---|---|---|
| react | 19.3.1 | MIT | runtime，打进 dist |
| react-dom | 19.3.1 | MIT | runtime，打进 dist |
| @playwright/test | 1.64.1 | Apache-2.0 | dev，e2e 用，不分发 |
| @types/react / @types/react-dom | 19.3.0 | MIT | dev，仅类型，不分发 |
| @vitejs/plugin-react | 5.2.0 | MIT | dev，不分发 |
| typescript | 5.8.3 | Apache-2.0 | dev，不分发 |
| vite | 7.3.7 | MIT | dev，不分发 |

注：node_modules 存在大量 extraneous 包（react-markdown、remark/micromark 系，
MIT/ISC）——不在 package.json 声明中，`frontend/src` 仅 import react 与
react-dom/client，残留不进 dist、不构成分发内容；建议后续 `npm prune` 清理（非本轮）。

## 四、dist 与外部二进制

- frontend/dist/（index.html + assets）随仓库提交，是 src 源码的构建产物；源码转
  AGPLv3 后 dist 自动被 AGPL 覆盖，含其中的 react/react-dom 编译产物（MIT 兼容，
  保留其版权声明即可）。
- ripgrep（rg）：仓库不 vendor、不链接，仅作为 PATH 上独立子进程调用
  （src/nexuscli/tools/builtins.py:200 只读命令白名单提及）。rg 本体为
  MIT OR Unlicense 双许可；经由子进程调用未修改的外部程序属「聚合」而非衍生作品，
  不触发其许可对 NexusCLI 的约束。此口径同样适用于 git 等其他 PATH 外部工具。

## 五、兼容性结论

**全部 41 个 Python 包 + 7 个 npm 声明包与 AGPLv3 目标兼容，未发现任何冲突项。**
逐项核对三类风险：① 无许可证/自定义许可——无（所有包均有 SPDX 可识别许可证）；
② GPL-2.0-only 类强互斥——无（最强为 certifi 的 MPL-2.0，文件级弱互斥，纳入 AGPL
工程无碍）；③ SSPL/CC 非分发包——无。宽松许可与 AGPLv3 单向兼容；转入 AGPL 时
保留各依赖版权与许可声明即可，无需替换任何依赖。
