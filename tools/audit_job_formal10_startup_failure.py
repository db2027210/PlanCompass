#!/usr/bin/env python3
"""Record the zero-counter JOB startup wrapper failure before retry."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
FORMAL = BASE / "formal10_after_round10_gate_20260729T1055"


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def main() -> int:
    log_path = FORMAL / "driver.log"
    log_text = log_path.read_text(encoding="utf-8")
    expected = "fresh_restart_r0/latest.json"
    if expected not in log_text or "FileNotFoundError" not in log_text:
        raise RuntimeError("FAIL_CLOSED: startup failure signature mismatch")
    observed = {
        "accepted": read_int(FORMAL / "state" / "formal_search_accepted_counter.txt"),
        "startup_baseline": read_int(
            FORMAL / "state" / "formal_startup_baseline_counter.txt"
        ),
        "formal_replay": read_int(BASE / "formal_search_replay_counter.txt"),
        "global_ordinal": read_int(BASE / "replay_counter.txt"),
    }
    if observed != {
        "accepted": 0,
        "startup_baseline": 0,
        "formal_replay": 130,
        "global_ordinal": 189,
    }:
        raise RuntimeError(f"FAIL_CLOSED: counters changed after startup failure: {observed}")
    audit = {
        "event": "formal_startup_technical_failure_before_counter_commit",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cause": "RA_PG_OUTPUT_ROOT pointed at isolated formal root, so the restart-baseline pointer was absent",
        "workload_reached": False,
        "counted_as_valid_replay": False,
        "counted_as_accepted": False,
        "counters_after": observed,
        "driver_log_path": str(log_path),
        "driver_log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
        "retry_fix": "restore RA_PG_OUTPUT_ROOT to attempt root; keep config output directory isolated",
    }
    path = FORMAL / "startup_failure_audit.json"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(audit, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        sys.exit(2)
