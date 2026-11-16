<!-- spec-format: 3 -->

# Docker 容器沙箱后端 - 项目范围

## Goal

- goal: 为 `sandbox.backend` 新增 `"docker"` 后端：经 `SandboxBackend` 协议（`available` / `probe` / `wrap`）把 shell 命令包进一次性容器执行——`--rm --read-only`、默认 `--network=none`、`--tmpfs /tmp`、cwd 与 `writable_paths` 逐路径挂载可写；CLI 缺失或 daemon 不可达时 `OperationUnsupported` fail-closed，绝不静默退化为 `none`。
- literal ask: 「利用 /spec 自动开工作流来执行」——用户 2026-11-17 会话指令，对象是上一轮遗留的中大项（源自 2026-11-13_fix-project-trust-boundary D7 P1「容器沙箱」）。
- stated purpose: 补齐 OS 级沙箱的容器后端缺口：现有 none/seatbelt/bubblewrap/landlock 覆盖 macOS 与 Linux 本机围栏，容器后端提供跨平台、强隔离的第三条路，且不依赖宿主内核特性。
- done means: `sandbox.backend="docker"` 可被 `resolve_backend` 解析并探测通过后生效；包裹命令带 `--rm --read-only --network=none --tmpfs /tmp`、cwd 挂载与 `writable_paths` 显式挂载；`allow_network=true` 时去掉 `--network=none`（容器网络整体放行，与 seatbelt 的仅出站粒度不对称，文档如实声明）；`SandboxConfig.docker_image`（默认 `python:3.11-slim`）可配镜像；fake which/runner 单测与文档契约测试全过；真实 daemon 冒烟因 daemon 未启动挂账。

## Charter

- V1 priority: quality > maintainability > speed > cost | charter.md
- V2 scope: strict | charter.md
- V3 verification: package | charter.md
- V4 finish: commit | charter.md
- V5 dependencies: none-new | charter.md
- V6 compatibility: keep-public-api | charter.md
- V7 blocked: park-and-continue | charter.md

## Scope

- in: src/nexuscli/policy/docker_sandbox.py, src/nexuscli/policy/os_sandbox.py, src/nexuscli/config.py, new:tests/test_docker_sandbox.py, src/nexuscli/tools/builtins.py, src/nexuscli/tools/commands.py, new:tests/test_docker_image_wiring.py, docs/permissions.md, README.md, new:tests/test_docker_sandbox_docs.py
- out: src/nexuscli/policy/linux_sandbox.py, src/nexuscli/entrypoints/**, frontend/**, pyproject.toml

## Assumptions

- A1 | `SUPPORTED_BACKENDS` 现为四值（none/seatbelt/bubblewrap/landlock），无容器后端 | fact | probe: `grep -n "SUPPORTED_BACKENDS" src/nexuscli/policy/os_sandbox.py` | CONFIRMED | if false: 以实际清单重估命名与派发点
- A2 | `SandboxConfig` 三字段（backend/writable_paths/allow_network），docstring 已定义 fail-closed 语义（请求不可用后端必须抛 `OperationUnsupported`，不退化为 none） | fact | probe: `sed -n '110,135p' src/nexuscli/config.py` + `sed -n '294,340p' src/nexuscli/policy/os_sandbox.py` | CONFIRMED | if false: 对齐实际配置面再定增量
- A3 | `resolve_backend(name, which=, runner=, platform=)` 是唯一派发点，Linux 后端经 `nexuscli.policy.linux_sandbox.resolve_linux_backend` 惰性导入，os_sandbox 不 eager import Linux 实现 | fact | probe: `sed -n '294,335p' src/nexuscli/policy/os_sandbox.py` | CONFIRMED | if false: docker 分支挂到实际派发处
- A4 | seatbelt 语义参照：cwd 与系统 temp 恒可写、显式 extras 才可写、网络默认拒绝、`allow_network` 才放行出站；`wrap_command(command, backend=, cwd=, writable_paths=, allow_network=, ...)` 是执行器入口 | fact | probe: `grep -n -A 10 "def wrap_command" src/nexuscli/policy/os_sandbox.py` + `sed -n '214,250p' src/nexuscli/policy/os_sandbox.py` | CONFIRMED | if false: 以实际 wrap 契约重写映射
- A5 | 本机 docker CLI 存在（client 29.x）但 daemon 未运行（docker.sock 不存在），真实容器冒烟当前不可行 | fact | probe: `docker version` （client 有版本、server 连接失败） | CONFIRMED | if false: daemon 在跑则把冒烟从 Parked 提为任务
- A6 | 既有沙箱测试用 fake which/runner 断言 argv 形状、不 spawn 真进程（test_os_sandbox.py / test_linux_sandbox.py 的注入模式） | fact | probe: `grep -n "def test_resolve_unknown_backend_fails_closed\|runner" tests/test_os_sandbox.py | head` | CONFIRMED | if false: 新测试沿用实际注入方式
- A7 | README 现行边界句为「OS 级沙箱仍挂账」，容器后端落地后应改为仅 Windows 后端挂账 | fact | probe: `grep -n "OS 级沙箱" README.md docs/permissions.md | head` | CONFIRMED | if false: 按现行措辞对齐

## Forks

- F1 | when: DockerBackend 放哪 | a) 新模块 `policy/docker_sandbox.py`，经 resolve_backend 惰性导入（镜像 linux_sandbox 的模块边界） b) 并入 os_sandbox.py | pick: a | by: V1 | reversal: reversible
- F2 | when: `allow_network` 的容器语义 | a) 去掉 `--network=none`，容器网络整体放行，文档如实声明与 seatbelt「仅出站」粒度的不对称（b 方案 iptables 细粒度限出站不可移植且复杂度不成比例） b) 容器内 iptables 细粒度限出站 | pick: a | by: V1 | reversal: reversible
- F3 | when: 默认镜像 | a) `python:3.11-slim`（与项目 Python pin 一致，命令多为 python 生态） b) `alpine`（最小但缺 python） | pick: a | by: V1 | reversal: reversible

## Criteria

- C1 | `"docker"` 后端按协议实现：包裹含 `--rm --read-only --network=none --tmpfs /tmp` 与 cwd/`writable_paths` 挂载；`allow_network` 去掉 network 隔离；镜像经 `SandboxConfig.docker_image` 配置；CLI 缺失或 probe 失败 `OperationUnsupported` fail-closed——全部有 fake 注入单测 | task-0001
- C2 | docs/permissions.md 后端清单与语义（含网络不对称、容器内 /tmp 与宿主 temp 不共享的边界）与 README 边界句同步，文档契约测试通过 | task-0002
- C3 | `SandboxConfig.docker_image` 从 bash 工具与 `CommandExecutor` 两个 wrap 调用点真实贯通到 backend（配置可换镜像端到端生效），wiring 测试通过 | task-0003

## Decisions

- D1 | C1 的「镜像经配置」需要改 builtins.py / commands.py 两个调用点，两文件原在 Scope.out——按 2026-11-16 会话「直接自己执行不用问」的常设授权与 runner.py 收编先例，chartter V2 临时调为 adjacent-fixes 后 plan-amend 收编 task-0003，事后复位 strict | by: user 2026-11-16/17 会话常设授权 | reversal: revert commit

## Parked

- P1 | 真实 docker daemon 冒烟（起容器跑一条命令验证端到端）：daemon 未运行，启动 Docker Desktop 与拉取镜像涉及用户环境与网络，不在本包内做 | by: A5 | reversal: daemon 可用后以独立小包/任务补冒烟

## Binding

## Binding
- branch: `spec/2026-11-17_add-docker-sandbox-backend`
- frozen: 2026-11-16T16:22:53+00:00
