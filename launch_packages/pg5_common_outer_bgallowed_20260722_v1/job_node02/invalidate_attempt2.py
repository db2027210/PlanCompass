#!/usr/bin/env python3
"""Seal JOB attempt2 as invalid after the formal resource-band correction."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess

import formal_pg_engine as engine
import formal_pg_point_runner as points


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
ROOT = LANE_ROOT / "history" / "job" / "baseline" / "R0" / "attempt_2"
FROZEN = LANE_ROOT / "bundle" / "attempt2_frozen"
CONTAINER = "ra_pg5_job_node02_v1_a2"
OWNED_PIDS = (48146, 48150, 48151, 48152, 48153, 49480)


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
        raise RuntimeError(f"owned attempt2 processes still alive: {alive}")
    if (ROOT / "point_result.json").exists():
        raise RuntimeError("attempt2 unexpectedly produced point_result.json")
    state_path = ROOT / "point_state.json"
    raw_path = ROOT / "measurement_resource_samples.jsonl"
    query_path = ROOT / "measurement_queries.jsonl"
    original_state_sha = sha256_file(state_path)
    raw_rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    query_rows = [json.loads(line) for line in query_path.read_text(encoding="utf-8").splitlines()]
    engine.SHARED_CONFIG = FROZEN
    points.SHARED_CONFIG = FROZEN
    samples = points._stability_samples_from_evidence(
        "job", "memory", raw_path, query_path, None
    )
    means = {
        field: statistics.fmean(float(row[f"{field}_utilization"]) for row in samples)
        for field in ("cpu", "memory", "io")
    }
    current = json.loads(state_path.read_text(encoding="utf-8"))
    inspect = json.loads(
        subprocess.run(
            ["docker", "inspect", CONTAINER],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
    )[0]
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    observed_cpu_cores = means["cpu"] * 3.0
    observed_memory_gib = means["memory"] * 3.25
    observed_io_mib_per_second = means["io"] * 1150.0
    disposition = {
        "schema_version": 1,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "lane_id": "job_node02",
        "workload": "job",
        "point_id": "R0",
        "attempt": 2,
        "status": "invalid_protocol_resource_band",
        "invalidated_at_utc": now,
        "reason": (
            "attempt2 was launched under superseded Memory 85-95%, CPU/IO <=80% "
            "bands; corrected formal bands are Memory 85-90% and CPU/IO 30-50%"
        ),
        "not_an_early_stop": True,
        "not_an_abnormal_workload_failure": True,
        "outer_allowed": False,
        "accepted_shrink_count": 0,
        "attempt1_modified": False,
        "process_stop": {
            "owned_pids": list(OWNED_PIDS),
            "all_owned_pids_exited": True,
            "external_processes_signaled": False,
            "container_intentionally_preserved": True,
        },
        "container_after_stop": {
            "id": inspect["Id"],
            "name": inspect["Name"].lstrip("/"),
            "status": inspect["State"]["Status"],
            "health": (inspect["State"].get("Health") or {}).get("Status"),
            "oom_killed": inspect["State"]["OOMKilled"],
            "restart_count": inspect["RestartCount"],
        },
        "retained_evidence": {
            "raw_resource_samples": {
                "path": str(raw_path),
                "sha256": sha256_file(raw_path),
                "count": len(raw_rows),
            },
            "query_log": {
                "path": str(query_path),
                "sha256": sha256_file(query_path),
                "count": len(query_rows),
                "error_count": sum(row.get("success") is not True for row in query_rows),
            },
            "original_point_state_sha256": original_state_sha,
            "background_logs": [
                str(LANE_ROOT / "background" / "attempt2_runtime_mpstat.log"),
                str(LANE_ROOT / "background" / "attempt2_runtime_iostat.log"),
                str(LANE_ROOT / "background" / "attempt2_runtime_vmstat.log"),
            ],
        },
        "observed_mean_utilization": means,
        "observed_absolute_demand": {
            "cpu_cores": observed_cpu_cores,
            "memory_working_set_gib": observed_memory_gib,
            "io_cap_equivalent_mib_per_second": observed_io_mib_per_second,
        },
        "corrected_formal_resource_bands": {
            "memory": [0.85, 0.90],
            "cpu": [0.30, 0.50],
            "io": [0.30, 0.50],
        },
        "demand_preserving_cap_ranges": {
            "cpu_quota_cores": [observed_cpu_cores / 0.50, observed_cpu_cores / 0.30],
            "memory_gib": [observed_memory_gib / 0.90, observed_memory_gib / 0.85],
            "io_mib_per_second": [
                observed_io_mib_per_second / 0.50,
                observed_io_mib_per_second / 0.30,
            ],
        },
        "attempt3_initial_envelope": {
            "cpu_quota_cores": 1.5,
            "memory_bytes": 3_221_225_472,
            "memory_swap_bytes": 3_221_225_472,
            "io_read_bps": 268_435_456,
            "io_write_bps": 268_435_456,
            "pids_limit": 512,
            "shared_buffers": "2GB",
            "effective_cache_size": "3GB",
        },
    }
    disposition_path = ROOT / "attempt_disposition.json"
    atomic_json(disposition_path, disposition)
    current.update(
        {
            "status": "invalid_protocol_resource_band",
            "invalidated_at_utc": now,
            "attempt_disposition_path": str(disposition_path),
            "attempt_disposition_sha256": sha256_file(disposition_path),
            "outer_allowed": False,
            "accepted_shrink_count": 0,
        }
    )
    atomic_json(state_path, current)
    print(json.dumps(disposition, ensure_ascii=False, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
