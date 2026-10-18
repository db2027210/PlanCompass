#!/usr/bin/env python3
"""Wait for direct isolation and absence of uncoordinated future-wave owners."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v1 as waiter
from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v6 as v56


UNCOORDINATED_CONTROLLER_PATTERNS = (
    re.compile(r"(?:^|\s)(?:bash\s+)?/home/anonymous/working/benchbase/"
               r"run_mixstrg_0809\.sh(?:\s|$)"),
)
ORIGINAL_APPEND = waiter.append


def is_uncoordinated_controller(command: str) -> bool:
    return any(pattern.search(command) for pattern in UNCOORDINATED_CONTROLLER_PATTERNS)


def uncoordinated_controller_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                .decode("utf-8", errors="replace").strip()
            )
            if not is_uncoordinated_controller(command):
                continue
            status = (entry / "status").read_text(
                encoding="utf-8", errors="replace",
            )
            ppid = re.search(r"^PPid:\s*(\d+)$", status, re.MULTILINE)
            cpus = re.search(
                r"^Cpus_allowed_list:\s*(\S+)$", status, re.MULTILINE,
            )
            rows.append({
                "pid": int(entry.name),
                "ppid": int(ppid.group(1)) if ppid else None,
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                "cpus_allowed_list": cpus.group(1) if cpus else "",
            })
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
    return sorted(rows, key=lambda row: int(row["pid"]))


def future_wave_controller_blockers(inventory: dict) -> list[str]:
    if uncoordinated_controller_rows():
        return ["external_uncoordinated_future_wave_controller_present"]
    return []


def append_with_future_wave_contract(path: Path, event: dict[str, Any]) -> None:
    enriched = dict(event)
    if event.get("event") == "formal_boundary_resume_wait_started":
        source = Path(__file__).resolve()
        enriched.update({
            "waiter_runtime_source_path": str(source),
            "waiter_runtime_source_sha256": hashlib.sha256(
                source.read_bytes()
            ).hexdigest(),
            "uncoordinated_controller_policy":
                "block_until_controller_exit_prevent_interwave_false_start",
            "uncoordinated_controller_pattern_count":
                len(UNCOORDINATED_CONTROLLER_PATTERNS),
        })
    if event.get("event") == "formal_boundary_resume_wait_sample":
        rows = uncoordinated_controller_rows()
        enriched["uncoordinated_future_wave_controllers"] = rows
        enriched["uncoordinated_future_wave_controller_count"] = len(rows)
    ORIGINAL_APPEND(path, enriched)


def main() -> int:
    # Install the v56 direct-isolation/load1 and persistent D-state policy,
    # while adding an independent prestart owner-liveness gate.
    waiter.controller_blockers = future_wave_controller_blockers
    waiter.append = append_with_future_wave_contract
    v56.ORIGINAL_APPEND = append_with_future_wave_contract
    return v56.main()


if __name__ == "__main__":
    raise SystemExit(main())
