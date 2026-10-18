#!/usr/bin/env python3
"""Run at most one evidence-backed successor batch per independent endpoint.

This is deliberately a finite scheduler, not a retry loop: it waits for the
two already-started fixed batches, audits them, and may start exactly the two
predeclared successors once.  A passing predecessor suppresses its successor.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/resource_adaptive/bin/python")

ITEMS = {
    "B5": {
        "predecessor": ROOT / "goal_20260720/formal_batches/B5_memory_explicitcap_n3_20260720",
        "successor_protocol": ROOT / "goal_20260720/protocols/B5_memory_calibrated_n3_20260720.json",
        "min_delta_pp": 6.0,
        "max_delta_pp": 12.0,
        "required_passes": 2,
    },
    "B6": {
        "predecessor": ROOT / "goal_20260720/formal_batches/B6_io_explicitcap_n2v2_20260720",
        "successor_protocol": ROOT / "goal_20260720/protocols/B6_io_systemknobs_n2_20260720.json",
        "min_delta_pp": 7.0,
        "max_delta_pp": 12.0,
        "required_passes": 1,
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def protocol_output(protocol: Path) -> Path:
    value = json.loads(protocol.read_text(encoding="utf-8"))
    destination = Path(value["output_dir"])
    return destination if destination.is_absolute() else (ROOT / destination).resolve()


def audit(batch: Path, item: dict[str, Any]) -> dict[str, Any]:
    command = [
        str(PYTHON), str(ROOT / "goal_20260720/verify_formal_batch.py"),
        "--batch-dir", str(batch),
        "--min-delta-pp", str(item["min_delta_pp"]),
        "--max-delta-pp", str(item["max_delta_pp"]),
        "--required-passes", str(item["required_passes"]), "--write",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"acceptance audit failed for {batch}: {completed.stderr[-2000:]}")
    return json.loads(completed.stdout)


def main() -> None:
    status_path = ROOT / "goal_20260720/recovery_schedule_status.json"
    state: dict[str, dict[str, Any]] = {
        name: {"predecessor_complete": False, "successor_started": False, "successor_complete": False}
        for name in ITEMS
    }
    processes: dict[str, subprocess.Popen[str]] = {}
    while True:
        for name, item in ITEMS.items():
            row = state[name]
            predecessor = item["predecessor"]
            if not row["predecessor_complete"] and (predecessor / "batch_summary.json").is_file():
                predecessor_audit = audit(predecessor, item)
                row["predecessor_complete"] = True
                row["predecessor_acceptance"] = predecessor_audit["acceptance_met"]
                row["predecessor_audit"] = str(predecessor / "acceptance_audit.json")
                if not predecessor_audit["acceptance_met"]:
                    protocol = item["successor_protocol"]
                    successor_output = protocol_output(protocol)
                    if successor_output.exists():
                        raise FileExistsError(f"one-shot successor output unexpectedly exists: {successor_output}")
                    processes[name] = subprocess.Popen([
                        str(PYTHON), str(ROOT / "goal_20260720/run_fixed_protocol_batch.py"),
                        "--protocol", str(protocol),
                    ], cwd=ROOT, text=True)
                    row["successor_started"] = True
                    row["successor_protocol"] = str(protocol)
                    row["successor_output"] = str(successor_output)
                    row["successor_pid"] = processes[name].pid
                else:
                    row["successor_skipped_reason"] = "predecessor_accepted"
            if row["successor_started"] and not row["successor_complete"]:
                successor = Path(row["successor_output"])
                process = processes[name]
                if process.poll() is not None:
                    row["successor_exit_code"] = process.returncode
                    if (successor / "batch_summary.json").is_file():
                        successor_audit = audit(successor, item)
                        row["successor_acceptance"] = successor_audit["acceptance_met"]
                        row["successor_audit"] = str(successor / "acceptance_audit.json")
                    row["successor_complete"] = True
        snapshot = {"updated_at_utc": now(), "policy": "one_successor_max_per_endpoint", "items": state}
        write_status(status_path, snapshot)
        done = all(
            row["predecessor_complete"] and (
                not row["successor_started"] or row["successor_complete"]
            ) for row in state.values()
        )
        if done:
            return
        time.sleep(15)


if __name__ == "__main__":
    main()
