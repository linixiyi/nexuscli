"""Launch the real NexusCLI runtime server (--workbench) with the offline demo
provider injected, for browser/e2e acceptance tests.

Ported from kitagent (MIT, zed123214/kitagent) ``scripts/web_validation_server.py``:
one READY JSON frame is written to the parent's pipe. Never save that frame in
logs — it carries the one-use bootstrap capability (a second copy must not
exist on stdout either, so the server's own banner lines, which include the
link, are captured and discarded here). A line on stdin requests graceful
shutdown.

Adaptation notes (stated, not silent):

- kitagent runs CoreApp as a subprocess and serves the web app in-process;
  NexusCLI's ``serve --workbench`` is a single RuntimeApiServer, so this
  script constructs that same server object in-process with the same
  arguments as ``nexuscli serve --workbench``
  (nexuscli/entrypoints/cli.py runtime_serve) and the offline provider
  injected through the llm.factory env seam (NEXUSCLI_OFFLINE_DEMO=1).
- kitagent pointed AGENTRT_DATA_ROOT into the temp dir; here HOME and
  USERPROFILE are both redirected (win32 Path.home() prefers USERPROFILE, a
  HOME-only redirect leaks) so runtime.db / tasks.db / sessions stay inside
  the throwaway root.
- kitagent's --slow maps to its AGENTRT_DEMO_INITIAL_DELAY/TOOL_DELAY; the
  demo chain here has no bash step, so --slow only raises the provider's
  per-chunk streaming delay.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

from nexuscli.config import load_config
from nexuscli.runtime.api import RuntimeApiServer

READY_TIMEOUT_SECONDS = 15.0


def free_port() -> int:
    """Ported verbatim from kitagent scripts/web_validation_server.py:24-27."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _prepare_workspace(root: Path) -> Path:
    """Seed the demo workspace (kitagent src/agent_runtime/web/demo.py:116-120).

    Exclusive creation prevents overwriting a user's existing workspace.
    """
    directory = root / "demo"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "README.md").write_text(
        "# 示例 Python 项目\n\n运行检查流程，再生成检查报告。\n", encoding="utf-8"
    )
    # 工作台静态托管按 <cwd>/frontend/dist 解析：把仓库构建产物同步进 demo 工作区，
    # 否则验证服务器首页恒为空态页（e2e UI 基线 4 条 skip 的根因，主会话收口修复）。
    import shutil

    repo_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if repo_dist.is_dir():
        shutil.copytree(repo_dist, directory / "frontend" / "dist", dirs_exist_ok=True)
    return directory


def _wait_ready(port: int, timeout: float) -> None:
    """Block until the server accepts TCP connections (kitagent's
    ``for _ in range(100): await gateway.describe()`` retry loop, adapted)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("NexusCLI runtime server did not become ready")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="HTTP port (0 = pick a free one)")
    parser.add_argument(
        "--slow",
        action="store_true",
        help="Slow the demo provider's streaming for human observation",
    )
    args = parser.parse_args()
    port = args.port or free_port()

    with tempfile.TemporaryDirectory(prefix="nexuscli-web-validation-") as root:
        # Full state isolation inside the throwaway root (see module docstring).
        os.environ["HOME"] = root
        os.environ["USERPROFILE"] = root
        # Offline provider injection through the llm.factory seam: the runtime
        # server's create_llm_client calls resolve to OfflineDemoProvider and
        # no network credential is ever contacted.
        os.environ["NEXUSCLI_OFFLINE_DEMO"] = "1"
        if args.slow:
            os.environ["NEXUSCLI_OFFLINE_DEMO_INITIAL_DELAY"] = "1.5"

        workspace = _prepare_workspace(Path(root))
        config = load_config(project_root=workspace)
        # The runtime server refuses to serve turns without an LLM key
        # (api.py _ensure_llm_key); the offline provider never uses it.
        config.llm.api_key = "offline-demo"
        server = RuntimeApiServer(
            cwd=str(workspace),
            config=config,
            api_key="offline-demo",
            port=port,
            workbench=True,
        )
        thread = threading.Thread(
            target=server.serve_forever,
            name="nexuscli-web-validation-server",
            daemon=True,
        )
        # Swallow the server's banner: its "打开一键链接完成认证" line embeds the
        # one-use token, and the READY frame below must stay the only copy on
        # the pipe (kitagent sent the subprocess stdout to DEVNULL for the same
        # reason). Restored in finally so a startup failure stays visible.
        real_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            thread.start()
            _wait_ready(port, READY_TIMEOUT_SECONDS)
        finally:
            sys.stdout = real_stdout

        origin = f"http://127.0.0.1:{port}"
        token = server.web_session.bootstrap_token
        bootstrap_url = f"{origin}/#bootstrap={token}"
        # READY frame (kitagent web_validation_server.py:73-76): the parent's
        # one-use launch capability. Never write it to a log file.
        print(
            "READY "
            + json.dumps(
                {"origin": origin, "token": token, "bootstrap_url": bootstrap_url},
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            sys.stdin.readline()
        except KeyboardInterrupt:
            pass
        finally:
            # Graceful shutdown: stop the workers, then break serve_forever.
            # RuntimeApiServer has no public stop surface; these two attrs are
            # the same levers its own finally-block uses (api.py serve_forever).
            server._stop.set()
            if server._server is not None:
                server._server.shutdown()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
