#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Iterable, List, Tuple


EXTERNAL_MARKERS = (
    "/disk2/resource_adaptive/binary_plan_tuning_hybenchxp",
    "/tmp/bonect_hyrt",
    "/tmp/bonect_single_probe",
    "/tmp/hw_",
    "/tmp/out_",
    "set_vm_resource.sh artifact-ubuntu20 32 12G 90 90",
    "run_neutral_hyxp_probe.py",
    "probe_hybenchxp_baseline.py",
    "hybenchxp_goal_supervisor.py",
    "monitor_hybenchxp_resource_sanity.py",
)

SELF_MARKERS = (
    "bonect_external_kill_guard.py",
    "grep -E",
    "ps -eo",
)


def _process_rows() -> Iterable[Tuple[int, int, str]]:
    out = subprocess.check_output(
        ["ps", "-eo", "pid=,pgid=,cmd="],
        text=True,
        errors="replace",
    )
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            pgid = int(parts[1])
        except ValueError:
            continue
        yield pid, pgid, parts[2]


def _targets() -> List[Tuple[int, int, str]]:
    targets: List[Tuple[int, int, str]] = []
    for pid, pgid, cmd in _process_rows():
        if pid == os.getpid():
            continue
        if any(marker in cmd for marker in SELF_MARKERS):
            continue
        if any(marker in cmd for marker in EXTERNAL_MARKERS):
            targets.append((pid, pgid, cmd))
    return targets


def _terminate(pid: int, pgid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for target in (pid, -pgid):
            try:
                os.kill(target, sig)
            except ProcessLookupError:
                pass
            except PermissionError:
                pass


def main() -> None:
    print("bonect_external_kill_guard enabled for strong baseline runs.", flush=True)
    while True:
        for pid, pgid, _cmd in _targets():
            _terminate(pid, pgid)
        time.sleep(2.0)


if __name__ == "__main__":
    main()
