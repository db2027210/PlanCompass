#!/usr/bin/env python3
from __future__ import annotations

import os
import runpy
import shlex
import signal
import sys
from pathlib import Path


def _ignore(_signum: int, _frame: object) -> None:
    return


def main() -> int:
    script = os.environ.get("BPT_SCRIPT", "")
    config = os.environ.get("BPT_CONFIG", "")
    output_dir = os.environ.get("BPT_OUTPUT_DIR", "")
    args_text = os.environ.get("BPT_ARGS", "")
    if not script or not config or not output_dir or not args_text:
        print(
            "BPT_SCRIPT, BPT_CONFIG, BPT_OUTPUT_DIR, and BPT_ARGS are required",
            file=sys.stderr,
            flush=True,
        )
        return 2

    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(signum, _ignore)
        except Exception:
            pass

    script_path = str(Path(script).resolve())
    sys.argv = [
        script_path,
        "--config",
        str(Path(config).resolve()),
        "--output-dir",
        str(Path(output_dir).resolve()),
        *shlex.split(args_text),
    ]
    runpy.run_path(script_path, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
