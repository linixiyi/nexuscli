# Docker 容器沙箱后端 - 任务拆解

## 使用规则

- 每个任务只改 `boundary` 列出的文件；发现相邻问题先记录，不顺手修。
- 机器任务的 `verify` 以首个反引号 span 为执行命令；命令必须在包工作区根目录可执行。
- fail-closed 是硬契约：请求 `"docker"` 而后端不可用（无 CLI / daemon 不可达 / probe 失败）必须抛 `OperationUnsupported`，绝不静默退化为 `none`——与 seatbelt 完全同构。
- 测试一律走 fake which/runner 注入断言 argv 形状，不 spawn 真 docker（daemon 本来就没跑）。
- 不改 linux_sandbox 与 seatbelt 的既有行为；不动 `SandboxBackend` 协议签名。

## 阶段 A：后端实现

- [x] task-0001 DockerBackend 与 resolve 派发
  - id: task-0001
  - boundary: new:src/nexuscli/policy/docker_sandbox.py, src/nexuscli/policy/os_sandbox.py, src/nexuscli/config.py, new:tests/test_docker_sandbox.py
  - origin: planned
  - expect: red
  - behavior: 新模块 `policy/docker_sandbox.py` 实现 `DockerBackend`（`available` = which("docker") 非空；`probe` 经注入 runner 跑 `docker version` 判 CLI+daemon 可达，10s 超时，对齐 `_PROBE_TIMEOUT_SECONDS` 语义；`wrap(SandboxRequest)` 返回 shell 字符串：`docker run --rm --read-only --network=none --tmpfs /tmp -v <abs-cwd>:<abs-cwd> -w <abs-cwd> [-v <p>:<p> ...] [--network=none 移除当 allow_network] <image> /bin/sh -c <shlex.quote(command)>`，`writable_paths` 去重、绝对化、经 shlex 安全处理，镜像取 `request` 携带的 `docker_image`（见下）；提供 `resolve_docker_backend(name, which=, runner=, platform=)` 镜像 `resolve_linux_backend` 形状）。`os_sandbox.py`：`BACKEND_DOCKER = "docker"` 入 `SUPPORTED_BACKENDS`，`resolve_backend` 加惰性导入分支；`wrap_command` 需把镜像从 config 传入（新增 keyword-only `docker_image: str = "python:3.11-slim"` 参数或经 `SandboxRequest` 携带，选不破坏现有调用方的方式）。`config.py`：`SandboxConfig.docker_image: str = "python:3.11-slim"` + docstring（首次使用需拉镜像、经配置可换）。
  - tests: 新增 `tests/test_docker_sandbox.py`（fake which/runner，参照 tests/test_os_sandbox.py 注入模式），至少覆盖：① wrap argv 形状：含 `--rm --read-only --network=none --tmpfs /tmp`、cwd 挂载与 `-w`、镜像名、`/bin/sh -c` 后接原命令；② `writable_paths` 逐路径挂载且去重、相对路径拒绝或绝对化（与 seatbelt 口径一致）；③ `allow_network=True` 时无 `--network=none`；④ `available()` 随 which 结果翻转；⑤ probe 失败/超时 → `resolve_docker_backend` 抛 `OperationUnsupported`；⑥ `resolve_backend("docker")` 惰性导入路径与未知后端报错信息含完整 SUPPORTED_BACKENDS；⑦ 命令含引号/分号时 shlex 转义 round-trip；⑧ `SandboxConfig.docker_image` 默认值与配置覆盖。
  - verify: `uv run --extra dev python -m pytest -q tests/test_docker_sandbox.py tests/test_os_sandbox.py tests/test_config.py`

## 阶段 B：文档同步

- [ ] task-0002 后端清单与如实边界文档
  - id: task-0002
  - boundary: docs/permissions.md, README.md, new:tests/test_docker_sandbox_docs.py
  - depends-on: task-0001
  - origin: planned
  - expect: red
  - behavior: docs/permissions.md「OS 级沙箱（sandbox.backend）」节：后端清单加 `"docker"`，说明容器语义（一次性容器、只读 rootfs、默认无网络、cwd/writable_paths 挂载、`sandbox.docker_image` 配置、首次使用需拉镜像）与两条如实边界——`allow_network` 是容器网络整体放行（seatbelt 可仅出站，粒度不对称）；容器内 `/tmp` 是 tmpfs、与宿主 temp 目录不共享（seatbelt 共享）。README 边界句从「OS 级沙箱仍挂账」改为仅 Windows 后端挂账的口径。措辞不得宣称 Windows 后端已支持。
  - tests: 新增 `tests/test_docker_sandbox_docs.py`：断言 permissions.md 含 docker 后端条目、`docker_image`、两条如实边界关键词；README 含 docker 后端与「Windows」挂账表述、不再有「OS 级沙箱仍挂账」整句旧口径。不依赖网络与 daemon。
  - verify: `uv run --extra dev python -m pytest -q tests/test_docker_sandbox_docs.py tests/test_docs_contract.py tests/test_docs_integrity.py tests/test_security_docs.py`

---

**当前进度**：由脚本计算，无需手动维护
- [ ] task-0003 docker_image 配置贯通到两个 wrap 调用点
  - id: task-0003
  - boundary: src/nexuscli/tools/builtins.py, src/nexuscli/tools/commands.py, new:tests/test_docker_image_wiring.py
  - depends-on: task-0001
  - origin: emergent
  - expect: red
  - behavior: C1「镜像经 SandboxConfig.docker_image 配置」的规划边界缺口收编：bash 工具的 `wrap_command(...)` 调用（builtins.py）补 `docker_image=context.config.sandbox.docker_image`；`CommandExecutor._apply_sandbox` 的 `SandboxRequest(...)`（commands.py）补 `docker_image=config.docker_image`。仅传参，不改其他逻辑。
  - verify: `uv run --extra dev python -m pytest -q tests/test_docker_image_wiring.py tests/test_tools.py`
