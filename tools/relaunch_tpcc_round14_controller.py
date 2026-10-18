#!/usr/bin/env python3
"""Relaunch the zero-call TPC-C round14 controller in a new session."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824"
)
CONTROLLER = Path(
    "/home/anonymous/program/resource_adaptive/tools/"
    "run_tpcc_round14_robust_controller.sh"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ledger_path = ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if (
        ledger.get("real_workload_invocation_count") != 0
        or ledger.get("successful") != []
        or ledger.get("technical_failures") != []
    ):
        raise RuntimeError("round14 is no longer a clean zero-call resume")
    completed = subprocess.run(
        ["pgrep", "-af", str(ROOT)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    active = [
        line
        for line in completed.stdout.splitlines()
        if line and str(os.getpid()) not in line
    ]
    if active:
        raise RuntimeError(f"round14 process already active: {active}")
    controller_sha = sha256(CONTROLLER)
    log_path = ROOT / "controller_relaunch.log"
    with log_path.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            [str(CONTROLLER)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (ROOT / "controller.pid").write_text(f"{process.pid}\n", encoding="utf-8")
    audit = {
        "event": "tpcc_round14_zero_call_controller_relaunch",
        "prior_controller_pid": 3300426,
        "new_controller_pid": process.pid,
        "physical_calls_before": 0,
        "controller_sha256": controller_sha,
        "new_workload_calls_by_relauncher": 0,
    }
    (ROOT / "zero_call_relaunch_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    time.sleep(3)
    if process.poll() is not None:
        raise RuntimeError(f"controller exited during relaunch: {process.returncode}")
    print(json.dumps(audit, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
