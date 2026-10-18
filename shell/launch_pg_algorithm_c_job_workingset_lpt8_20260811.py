#!/usr/bin/env python3
"""Launch JOB with working-set scoring and eight-client LPT scheduling.

This wrapper is intentionally JOB-only.  It changes constants on the private
launcher module instance, so live XP controllers keep their frozen source and
runtime configuration unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict

import launch_pg_algorithm_c_job_workingset_capacity_20260811 as capacity


JOB_REPLAY_CLIENTS = 8
JOB_MAX_CONCURRENT_LONG_QUERIES = 8
SCHEDULING_CONTRACT = "canonical_job_113_historical_lpt_8_clients_v1"


def configure_private_launcher() -> None:
    """Configure only the imported launcher instance used by this process."""
    capacity.base.JOB_REPLAY_CLIENTS = JOB_REPLAY_CLIENTS
    capacity.base.JOB_MAX_CONCURRENT_LONG_QUERIES = (
        JOB_MAX_CONCURRENT_LONG_QUERIES
    )


def prepare(tag: str) -> Path:
    configure_private_launcher()
    run_root = capacity.prepare(tag)
    scene_dir = run_root / "job_memory"
    config_path = scene_dir / "optimizer_config.json"
    contract_path = scene_dir / "workload_contract.json"

    scheduling: Dict[str, object] = {
        "contract": SCHEDULING_CONTRACT,
        "canonical_query_count": 113,
        "exactly_once": True,
        "shared_database_per_replay": True,
        "task_dispatch": "single_shared_queue",
        "ordering": "historical_baseline_longest_processing_time_first",
        "workers_per_replay": JOB_REPLAY_CLIENTS,
        "maximum_concurrent_long_queries": JOB_MAX_CONCURRENT_LONG_QUERIES,
        "parallel_real_replays": 2,
        "target_wall_time_s": 900.0,
        "query_subset_or_sampling": False,
        "qps_used_for_resource_prediction": False,
    }

    cfg = capacity.base.read_json(config_path)
    cfg["binary_plan_tuning"]["job_replay_scheduling"] = dict(scheduling)
    cfg["algorithm_c"]["workload_contract"]["job_replay_scheduling"] = dict(
        scheduling
    )
    capacity.base.write_json(config_path, cfg)

    contract = capacity.base.read_json(contract_path)
    contract["job_replay_scheduling"] = dict(scheduling)
    capacity.base.write_json(contract_path, contract)

    manifest_path = run_root / "launch_manifest.json"
    manifest = capacity.base.read_json(manifest_path)
    wrapper_path = Path(__file__).resolve()
    manifest["source_provenance"]["files"][
        str(wrapper_path.relative_to(capacity.base.ROOT))
    ] = capacity.base.sha256(wrapper_path)
    manifest["job_replay_scheduling"] = dict(scheduling)
    capacity.base.write_json(manifest_path, manifest)
    return run_root


def launch(tag: str) -> Dict[str, int]:
    configure_private_launcher()
    return capacity.launch(tag)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "launch"))
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare(args.tag), flush=True)
        return 0
    pids = launch(args.tag)
    print(
        json.dumps(
            {"run_root": str(capacity.base.RUN_BASE / args.tag), "pids": pids}
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
