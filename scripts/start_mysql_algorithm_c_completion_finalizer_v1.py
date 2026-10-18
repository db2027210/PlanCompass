#!/usr/bin/env python3
"""Start the Algorithm C completion finalizer as a detached host process."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("usage: starter RUN_PREFIX")
    prefix = sys.argv[1]
    campaign = HISTORY / prefix
    pid_path = campaign / "completion_finalizer.pid"
    if pid_path.is_file():
        old_pid = int(pid_path.read_text(encoding="utf-8").strip())
        cmdline_path = Path(f"/proc/{old_pid}/cmdline")
        cmdline = (
            cmdline_path.read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if cmdline_path.is_file()
            else ""
        )
        if (
            "finalize_mysql_algorithm_c_after_monitor_v1.py" in cmdline
            and prefix in cmdline
        ):
            raise RuntimeError(f"completion finalizer already alive: {old_pid}")
    log = (campaign / "completion_finalizer.log").open("ab", buffering=0)
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(ROOT / "scripts/finalize_mysql_algorithm_c_after_monitor_v1.py"),
            prefix,
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    print(process.pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
