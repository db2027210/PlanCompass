#!/usr/bin/env python3
"""Seal the interrupted a5 calibration after raw-utilization semantics changed."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_5"
OWNED_PIDS = (79430, 79435, 79436, 79437, 79438, 81283, 81285)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    alive = [pid for pid in OWNED_PIDS if Path(f"/proc/{pid}").exists()]
    if alive:
        raise RuntimeError(f"a5 owned processes remain: {alive}")
    if subprocess.run(
        ["docker", "inspect", "ra_pg5_job_node02_v1_a5"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0:
        raise RuntimeError("a5 container unexpectedly remains")
    result_path = ROOT / "point_result.json"
    gate_path = ROOT / "baseline_gate_decision.json"
    state_path = ROOT / "point_state.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    if result.get("measurement", {}).get("effective_measurement_seconds", 600) >= 180:
        raise RuntimeError("a5 unexpectedly reached the formal minimum")
    if gate.get("outer_allowed") is not False:
        raise RuntimeError("a5 gate was not fail closed")
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    retained_names = (
        "point_result.json",
        "baseline_gate_decision.json",
        "measurement_resource_samples.jsonl",
        "measurement_queries.jsonl",
        "measurement_summary.json",
        "measurement_online_stability_checks.json",
    )
    retained = {
        name: {"path": str(ROOT / name), "sha256": sha256_file(ROOT / name)}
        for name in retained_names
        if (ROOT / name).is_file()
    }
    disposition = {
        "schema_version": 1,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "lane_id": "job_node02",
        "workload": "job",
        "point_id": "R0",
        "attempt": 5,
        "status": "invalid_metric_semantics",
        "invalidated_at_utc": now,
        "reason": (
            "a5 loaded the superseded raw-utilization validator that rejected "
            "values above 1 and clipped persisted utilization to 1"
        ),
        "formal_minimum_reached": False,
        "effective_measurement_seconds": result["measurement"][
            "effective_measurement_seconds"
        ],
        "outer_allowed": False,
        "accepted_shrink_count": 0,
        "attempt4_retroactive_acceptance": False,
        "attempt5_retroactive_acceptance": False,
        "process_stop": {
            "owned_pids": list(OWNED_PIDS),
            "all_owned_pids_exited": True,
            "external_processes_signaled": False,
            "measurement_process_group_signaled": 81285,
        },
        "cleanup": result.get("cleanup"),
        "container_absent_after_cleanup": True,
        "retained_evidence": retained,
        "superseding_semantics": {
            "raw_utilization_validation": "finite_and_non_negative_no_upper_bound",
            "raw_utilization_persistence": "original_value_without_clipping",
            "target_resource_gate": "memory_strictly_above_0.80_no_upper_bound",
            "non_target_gate": {"cpu": [0.30, 0.50], "io": [0.30, 0.50]},
        },
    }
    disposition_path = ROOT / "attempt_disposition.json"
    atomic_json(disposition_path, disposition)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "status": "invalid_metric_semantics",
            "invalidated_at_utc": now,
            "attempt_disposition_path": str(disposition_path),
            "attempt_disposition_sha256": sha256_file(disposition_path),
            "outer_allowed": False,
            "accepted_shrink_count": 0,
        }
    )
    atomic_json(state_path, state)
    print(json.dumps(disposition, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
