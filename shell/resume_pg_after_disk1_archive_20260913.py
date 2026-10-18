#!/usr/bin/env python3
"""Resume only the explicitly paused replay after verified archival and quiet gates."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shell.archive_disk1_quarantine_20260913 import REPORT, SOURCES
from shell.run_pg_common_domain_frozen_champions import (
    diskstats_device_snapshot,
    scan_external_workload_processes,
)

STATUS = ROOT / "docs/artifacts/disk1_archive_replay_resume_20260913.json"
COHORT = "/disk1/resource_adaptive/pg_paper_domain_isolated_abc_20260913/r2_wait_resume"
QUEUE_PID = 2490011
RUNNER_PID = 2490012
ARCHIVER_PID = 2505012


def process_identity(pid: int) -> Dict[str, Any]:
    base = Path("/proc") / str(pid)
    fields = (base / "stat").read_text().rsplit(") ", 1)[1].split()
    return {"pid": pid, "state": fields[0], "start_ticks": fields[19],
            "argv": (base / "cmdline").read_bytes().split(b"\0")[:-1]}


def check_identity(expected: Dict[str, Any], *, stopped: bool = False) -> None:
    current = process_identity(expected["pid"])
    if any(current[key] != expected[key] for key in ("start_ticks", "argv")):
        raise RuntimeError(f"process identity changed: {expected['pid']}")
    if stopped and current["state"] not in ("T", "t"):
        raise RuntimeError(f"replay is no longer paused: {expected['pid']}")
    if current["state"] == "Z":
        raise RuntimeError(f"process exited: {expected['pid']}")


def validate_archive(report: Dict[str, Any]) -> None:
    if report.get("phase") != "complete":
        raise RuntimeError("archive has not completed")
    rows = report.get("rows", [])
    if [row["source"] for row in rows] != [str(source) for source in SOURCES]:
        raise RuntimeError("archive source scope mismatch")
    for row in rows:
        if row.get("remaining", {}).get("files") != 0:
            raise RuntimeError("archive source still contains files")
        for key in ("files", "logical_bytes"):
            if row.get("archived", {}).get(key) != row["before"][key]:
                raise RuntimeError(f"archive inventory mismatch: {key}")


def save(phase: str, **fields: Any) -> None:
    record = {"phase": phase, "updated_at_epoch_s": time.time(),
              "cohort": COHORT, "queue_pid": QUEUE_PID, "runner_pid": RUNNER_PID,
              "archive_report": str(REPORT), **fields}
    temporary = STATUS.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(STATUS)


def main() -> None:
    queue, runner, archiver = map(process_identity, (QUEUE_PID, RUNNER_PID, ARCHIVER_PID))
    if (COHORT.encode() not in queue["argv"]
            or not any(arg.endswith(b"/run_pg_paper_domain_isolated_abc.sh") for arg in queue["argv"])
            or (COHORT + "/xp_cpu").encode() not in runner["argv"]
            or not any(arg.endswith(b"/run_pg_common_domain_frozen_champions.py") for arg in runner["argv"])
            or not any(arg.endswith(b"archive_disk1_quarantine_20260913.py") for arg in archiver["argv"])
            or b"--execute" not in archiver["argv"]):
        raise RuntimeError("unexpected process command; refusing automatic resume")
    for process in (queue, runner):
        check_identity(process, stopped=True)
    while True:
        report = json.loads(REPORT.read_text())
        if report.get("phase") == "complete":
            validate_archive(report)
            break
        check_identity(archiver)
        for process in (queue, runner):
            check_identity(process, stopped=True)
        save("waiting_for_archive", completed_trees=sum("archived" in row for row in report["rows"]))
        time.sleep(15)

    # Start a fresh quiet interval: suspended time never counts as quiet time.
    quiet_since = time.monotonic()
    disk_since = quiet_since
    before = diskstats_device_snapshot("nvme1n1")
    disk_passed = False
    util_pct = None
    last_notice = 0.0
    while True:
        matches, errors = scan_external_workload_processes()
        now = time.monotonic()
        if matches or errors:
            quiet_since = now
        if now - disk_since >= 15:
            after = diskstats_device_snapshot("nvme1n1")
            util_pct = max(0, after["busy_ms"] - before["busy_ms"]) / ((now - disk_since) * 10)
            disk_passed = util_pct <= 5
            disk_since, before = now, after
        passed = not matches and not errors and now - quiet_since >= 120 and disk_passed
        save("quiet_gate_passed" if passed else "waiting_for_quiet",
             external_active_count=len(matches), scan_errors=errors,
             external_quiet_s=now - quiet_since, required_external_quiet_s=120,
             ssd_util_pct=util_pct, maximum_ssd_util_pct=5)
        if passed:
            break
        if now - last_notice >= 30:
            print(f"Waiting: external={len(matches)}, quiet={now - quiet_since:.0f}/120s, "
                  f"SSD util={util_pct}", flush=True)
            last_notice = now
        time.sleep(1)
    # Final fail-closed check immediately before signalling this exact cohort.
    validate_archive(json.loads(REPORT.read_text()))
    for process in (queue, runner):
        check_identity(process, stopped=True)
    os.kill(RUNNER_PID, signal.SIGCONT)
    os.kill(QUEUE_PID, signal.SIGCONT)
    save("resumed", archive_completed_at_epoch_s=report["completed_at_epoch_s"],
         external_quiet_s=time.monotonic() - quiet_since, ssd_util_pct=util_pct)
    print("Archive verified and quiet gates passed; replay resumed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        save("blocked_no_automatic_resume", error=f"{type(exc).__name__}: {exc}")
        raise
