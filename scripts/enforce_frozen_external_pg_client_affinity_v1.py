#!/usr/bin/env python3
"""Enforce declared client CPU sets for frozen co-resident PG campaigns.

The guard is deliberately narrow.  It discovers disk1 PostgreSQL controllers
whose optimizer config declares ``algorithm_c.controller_client_cpuset`` and
only corrects client descendants (or cohort-support clients whose command
contains the matching frozen campaign/scene path).  PostgreSQL backends,
containers, databases, and unrelated commands are never modified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any


DISK1_ROOT = Path("/disk1/resource_adaptive")
MYSQL_TARGET_CPUS = frozenset(
    (*range(0, 32), *range(104, 128), *range(200, 228),
     *range(256, 288), *range(360, 384), *range(456, 484))
)
CLIENT_MARKERS = (
    "com.hybench.HyBench", "stmt_postgres.toml", "benchbase.jar", " psql ",
)
CONFIG_PATTERN = re.compile(r"(?:^|\s)--config\s+(\S+/optimizer_config\.json)(?:\s|$)")
CAMPAIGN_PATTERN = re.compile(r"(?:^|\s)--campaign-root\s+(\S+)(?:\s|$)")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def parse_cpus(value: str) -> frozenset[int]:
    result: set[int] = set()
    for field in value.split(","):
        start, separator, end = field.strip().partition("-")
        if not start:
            continue
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return frozenset(result)


def append(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def proc_rows() -> tuple[dict[int, int], dict[int, str]]:
    parents: dict[int, int] = {}
    commands: dict[int, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
            parent = re.search(r"^PPid:\s*(\d+)$", status, re.MULTILINE)
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace").strip()
            )
            if parent and command:
                parents[int(entry.name)] = int(parent.group(1))
                commands[int(entry.name)] = command
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    return parents, commands


def load_declared_cpuset(config_path: Path) -> frozenset[int]:
    resolved = config_path.resolve()
    if (
        not resolved.is_file()
        or resolved.is_symlink()
        or DISK1_ROOT not in resolved.parents
        or resolved.name != "optimizer_config.json"
    ):
        raise RuntimeError(f"untrusted external optimizer config: {resolved}")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    text = str(((value.get("algorithm_c") or {}).get("controller_client_cpuset") or ""))
    cpuset = parse_cpus(text)
    if not cpuset or cpuset & MYSQL_TARGET_CPUS:
        raise RuntimeError(f"external client cpuset overlaps MySQL: {resolved}: {text}")
    return cpuset


def discover_domains(
    commands: dict[int, str],
) -> tuple[dict[int, frozenset[int]], list[tuple[str, frozenset[int]]]]:
    """Return controller roots and exact campaign/scene command markers."""

    roots: dict[int, frozenset[int]] = {}
    markers: list[tuple[str, frozenset[int]]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for pid, command in commands.items():
        match = CONFIG_PATTERN.search(command)
        if match:
            config = Path(match.group(1)).resolve()
            try:
                cpuset = load_declared_cpuset(config)
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                continue
            roots[pid] = cpuset
            scene_dir = config.parent
            campaign_root = scene_dir.parent
            for marker in (
                str(scene_dir) + "/",
                str(campaign_root / "cohort_support" / scene_dir.name) + "/",
            ):
                key = (marker, tuple(sorted(cpuset)))
                if key not in seen:
                    markers.append((marker, cpuset))
                    seen.add(key)
        campaign = CAMPAIGN_PATTERN.search(command)
        if not campaign:
            continue
        campaign_root = Path(campaign.group(1)).resolve()
        if DISK1_ROOT not in campaign_root.parents:
            continue
        for scene_dir in sorted(campaign_root.iterdir() if campaign_root.is_dir() else []):
            config = scene_dir / "optimizer_config.json"
            if not config.is_file() or config.is_symlink():
                continue
            try:
                cpuset = load_declared_cpuset(config)
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError):
                continue
            marker = str(campaign_root / "cohort_support" / scene_dir.name) + "/"
            key = (marker, tuple(sorted(cpuset)))
            if key not in seen:
                markers.append((marker, cpuset))
                seen.add(key)
    markers.sort(key=lambda item: len(item[0]), reverse=True)
    return roots, markers


def descendant_domains(
    parents: dict[int, int], roots: dict[int, frozenset[int]],
) -> dict[int, frozenset[int]]:
    result = dict(roots)
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if pid not in result and parent in result:
                result[pid] = result[parent]
                changed = True
    return result


def desired_client_cpus(
    pid: int,
    command: str,
    descendants: dict[int, frozenset[int]],
    markers: list[tuple[str, frozenset[int]]],
) -> frozenset[int] | None:
    if str(DISK1_ROOT) not in command or command.startswith("postgres: "):
        return None
    if not any(marker in f" {command} " for marker in CLIENT_MARKERS):
        return None
    for marker, cpuset in markers:
        if marker in command:
            return cpuset
    return descendants.get(pid)


def enforce_once(log: Path) -> dict[str, int]:
    parents, commands = proc_rows()
    roots, markers = discover_domains(commands)
    descendants = descendant_domains(parents, roots)
    corrected = 0
    matched = 0
    for pid, command in sorted(commands.items()):
        desired = desired_client_cpus(pid, command, descendants, markers)
        if desired is None:
            continue
        matched += 1
        try:
            previous = frozenset(os.sched_getaffinity(pid))
            if previous == desired:
                continue
            os.sched_setaffinity(pid, desired)
            readback = frozenset(os.sched_getaffinity(pid))
            event = {
                "captured_at": now(),
                "event": "frozen_external_pg_client_affinity_corrected",
                "pid": pid,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "previous_cpus": sorted(previous),
                "declared_cpus": sorted(desired),
                "readback_cpus": sorted(readback),
                "passed": readback == desired,
            }
            append(log, event)
            if readback != desired:
                raise RuntimeError(f"external PG client affinity readback differs: {pid}")
            corrected += 1
        except (FileNotFoundError, ProcessLookupError):
            continue
    return {"matched": matched, "corrected": corrected, "domains": len(roots)}


def unit_active(unit: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", unit],
        check=False, timeout=10,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, default=0.05)
    parser.add_argument("--formal-unit")
    parser.add_argument("--startup-grace-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds <= 0 or args.startup_grace_seconds <= 0:
        parser.error("positive intervals required")
    append(args.log, {
        "captured_at": now(), "event": "frozen_external_pg_affinity_guard_started",
        "pid": os.getpid(), "formal_unit": args.formal_unit,
        "interval_seconds": args.interval_seconds,
        "mysql_target_cpus": sorted(MYSQL_TARGET_CPUS),
    })
    deadline = time.monotonic() + args.startup_grace_seconds
    saw_formal_active = False
    last = {"matched": 0, "corrected": 0, "domains": 0}
    while True:
        last = enforce_once(args.log)
        if args.once:
            break
        if args.formal_unit:
            active = unit_active(args.formal_unit)
            saw_formal_active = saw_formal_active or active
            if saw_formal_active and not active:
                break
            if not saw_formal_active and time.monotonic() >= deadline:
                raise RuntimeError("formal unit did not become active before guard deadline")
        time.sleep(args.interval_seconds)
    append(args.log, {
        "captured_at": now(), "event": "frozen_external_pg_affinity_guard_stopped",
        "pid": os.getpid(), "last_inventory": last,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
