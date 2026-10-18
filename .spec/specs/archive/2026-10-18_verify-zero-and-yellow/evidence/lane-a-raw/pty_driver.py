"""Lane A one-off pty driver: runs real REPL scenarios with an isolated HOME.

Not a test — evidence gatherer for docs/testing-matrix.md lane A.
Usage: python3 lane_a_pty.py <scenario> <runhome> <project>
Scenarios: save | resume_a | resume_b | fork | snap_save | snap_restore | vfs
"""

import os
import pty
import re
import select
import struct
import fcntl
import termios
import sys
import time
import signal

EXEC = "/Users/linyixi/nexuscli/.venv/bin/nexuscli"


def drive(script, home, cwd, cols=120, rows=40, idle=8.0):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["HOME"] = home
        os.environ["TERM"] = "xterm-256color"
        os.chdir(cwd)
        os.execvp(EXEC, ["nexuscli"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = b""

    def drain(sec):
        nonlocal out
        end = time.time() + sec
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return False
        return True

    for wait, keys in script:
        if not drain(wait):
            break
        if keys:
            os.write(fd, keys.encode())
    drain(idle)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    return out


ANSI_CSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]")
ANSI_OSC = re.compile(rb"\x1b\][^\x07\x1b]*(\x07|\x1b\\)")
ANSI_SIMPLE = re.compile(rb"\x1b[=>NOPc78MD]")


def clean(raw: bytes) -> str:
    for pat in (ANSI_OSC, ANSI_CSI, ANSI_SIMPLE):
        raw = pat.sub(b"", raw)
    return raw.decode("utf-8", errors="replace")


def main() -> None:
    scenario, runhome, project = sys.argv[1], sys.argv[2], sys.argv[3]
    scripts = {
        # /save: one minimal real turn (makes a session), then /save a fact.
        "save": [(4, ""), (35, "请只回复两个字:好的\r"), (3, "/save 项目使用 uv 管理依赖,不用 pip\r"), (2, "/exit\r")],
        # /resume process A: plant a codeword, exit.
        "resume_a": [(4, ""), (40, "听好了,暗号是柠檬七号。这只是对话内容,不要调用任何工具,直接只回复两个字:收到\r"), (3, "n\r"), (2, "/exit\r")],
        # /resume process B: list sessions, resume #1, ask for the codeword.
        "resume_b": [(4, "/resume\r"), (3, "/resume 1\r"), (40, "暗号是什么?只回答暗号本身\r"), (2, "/exit\r")],
        # /fork: turn in main line, fork, one more turn in the fork only.
        "fork": [
            (4, ""),
            (35, "记住暗号:苹果九号。只回复:收到\r"),
            (3, "/fork 实验方向\r"),
            (35, "把暗号改成:梨十号。只回复:收到\r"),
            (2, "/exit\r"),
        ],
        # /snapshot: manual save with a label, then the listing.
        "snap_save": [(4, "/snapshot save 改坏前\r"), (2, "/snapshot\r"), (2, "/exit\r")],
        # /restore after the file was broken outside the REPL.
        "snap_restore": [(4, "/restore 1\r"), (2, "/snapshot\r"), (2, "/exit\r")],
        # vfs: snapshot, break the file, then ask the agent (real gateway) to
        # compare via the read-only view.
        "vfs": [
            (4, "/snapshot save 对比前\r"),
            (2, "/exit\r"),
        ],
        "vfs_read": [
            (4, ""),
            (
                60,
                "请用 vfs_ls 工具列出快照 1 的根目录,再用 vfs_read 工具读取其中 note.txt 的内容,"
                "把读到的文件内容原样贴在你的回答里\r",
            ),
            (2, "/exit\r"),
        ],
    }
    out = drive(scripts[scenario], home=runhome, cwd=project)
    os.makedirs("/tmp/lane_a_raw", exist_ok=True)
    path = f"/tmp/lane_a_raw/{scenario}.log"
    with open(path, "wb") as handle:
        handle.write(out)
    text = clean(out)
    print(f"===== {scenario} (cleaned, {len(text)} chars) =====")
    print(text)


if __name__ == "__main__":
    main()
