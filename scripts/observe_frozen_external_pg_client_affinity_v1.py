#!/usr/bin/env python3
"""Observe frozen external PG client affinity without mutating any process."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import enforce_frozen_external_pg_client_affinity_v1 as discovery


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def observe_once(log: Path) -> dict[str, int]:
    parents, commands = discovery.proc_rows()
    roots, markers = discovery.discover_domains(commands)
    descendants = discovery.descendant_domains(parents, roots)
    matched = mismatched = 0
    for pid, command in sorted(commands.items()):
        desired = discovery.desired_client_cpus(pid, command, descendants, markers)
        if desired is None:
            continue
        matched += 1
        try:
            observed = frozenset(os.sched_getaffinity(pid))
        except (FileNotFoundError, ProcessLookupError):
            continue
        if observed == desired:
            continue
        mismatched += 1
        append(log, {
            "captured_at": now(),
            "event": "external_pg_client_affinity_mismatch_observed_read_only",
            "pid": pid,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "observed_cpus": sorted(observed),
            "declared_cpus": sorted(desired),
            "external_mutation": False,
            "passed": False,
        })
    return {
        "matched": matched,
        "mismatched": mismatched,
        "corrected": 0,
        "domains": len(roots),
    }


def unit_active(unit: str) -> bool | None:
    """Return the formal-unit state without treating a D-Bus timeout as exit.

    The user manager can be temporarily slow while all four formal scenes are
    launching.  A timeout is not evidence that the formal controller stopped,
    so callers retain their last known state and keep observing.
    """
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", unit],
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return None
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=0.25)
    parser.add_argument("--formal-unit")
    parser.add_argument("--startup-grace-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.startup_grace_seconds <= 0:
        parser.error("positive intervals required")
    append(args.log, {
        "captured_at": now(),
        "event": "external_pg_affinity_observer_started",
        "pid": os.getpid(),
        "formal_unit": args.formal_unit,
        "interval_seconds": args.interval_seconds,
        "external_mutation": False,
    })
    deadline = time.monotonic() + args.startup_grace_seconds
    saw_formal_active = False
    last = {"matched": 0, "mismatched": 0, "corrected": 0, "domains": 0}
    while True:
        last = observe_once(args.log)
        if args.once:
            break
        if args.formal_unit:
            active = unit_active(args.formal_unit)
            if active is not None:
                saw_formal_active = saw_formal_active or active
            if saw_formal_active and active is False:
                break
            if not saw_formal_active and time.monotonic() >= deadline:
                raise RuntimeError("formal unit did not become active before observer deadline")
        time.sleep(args.interval_seconds)
    append(args.log, {
        "captured_at": now(),
        "event": "external_pg_affinity_observer_stopped",
        "pid": os.getpid(),
        "last_inventory": last,
        "external_mutation": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
