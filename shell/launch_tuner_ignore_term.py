#!/usr/bin/env python3
from __future__ import annotations

import runpy
import signal
import sys
from pathlib import Path


def _ignore(_signum: int, _frame: object) -> None:
    return


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: launch_tuner_ignore_term.py <script> [args...]", file=sys.stderr, flush=True)
        return 2
    for signum in (signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(signum, _ignore)
        except Exception:
            pass
    script = str(Path(sys.argv[1]).resolve())
    sys.argv = [script, *sys.argv[2:]]
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
