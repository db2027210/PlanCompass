#!/usr/bin/env python3
"""Fail closed on live XP processes while ignoring unreapable zombies."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


def active_matches(
    processes: Iterable[Mapping[str, Any]],
    patterns: Sequence[str],
) -> list[Dict[str, Any]]:
    compiled = [re.compile(pattern) for pattern in patterns]
    matches = []
    for process in processes:
        state = str(process.get("state") or "")[:1]
        command = str(process.get("command") or "")
        if state == "Z" or not command:
            continue
        if any(pattern.search(command) for pattern in compiled):
            matches.append(
                {
                    "pid": int(process["pid"]),
                    "state": state,
                    "command": command,
                }
            )
    return matches


def _read_stat(path: Path) -> tuple[int, int, str]:
    value = path.read_text(encoding="utf-8")
    close = value.rfind(")")
    if close < 0:
        raise RuntimeError(f"malformed proc stat: {path}")
    pid = int(value[: value.find(" ")])
    fields = value[close + 2 :].split()
    return pid, int(fields[1]), fields[0]


def _ancestor_pids(pid: int) -> set[int]:
    output = {pid}
    current = pid
    while current > 1:
        try:
            _, parent, _ = _read_stat(Path(f"/proc/{current}/stat"))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            break
        if parent <= 0 or parent in output:
            break
        output.add(parent)
        current = parent
    return output


def proc_processes(*, excluded_pids: set[int]) -> list[Dict[str, Any]]:
    output = []
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid, _, state = _read_stat(stat_path)
            if pid in excluded_pids:
                continue
            command = (
                stat_path.with_name("cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
                .strip()
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        output.append({"pid": pid, "state": state, "command": command})
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", action="append", required=True)
    args = parser.parse_args()
    processes = proc_processes(excluded_pids=_ancestor_pids(os.getpid()))
    active = active_matches(processes, args.pattern)
    zombies = [
        process
        for process in processes
        if str(process.get("state") or "")[:1] == "Z"
    ]
    print(
        json.dumps(
            {
                "active_match_count": len(active),
                "active_matches": active,
                "contract": "xp_v34_non_zombie_process_guard_v1",
                "zombie_process_count_audit_only": len(zombies),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 75 if active else 0


if __name__ == "__main__":
    raise SystemExit(main())
