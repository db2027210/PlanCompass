#!/usr/bin/env python3
"""Bind unchanged IO workers to the combined CPU-gap resource freeze."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_xp4_live_authority_v1 as authority
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4


FREEZE = ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v3/resource_successor.json"
SOURCE = ROOT / "history/mysql_four_scene_20260809/v186_to_c_top4_io_xp4_20260826_attempt013_xp_io/xp_resets/v18_pruning_v2_formal_incumbent_baseline/reset_binding.json"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/v216_to_xp_io_combined_freeze_transition_20260826/reset_binding.json"


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh IO transition binding required: {OUTPUT}")
    freeze = authority.load(FREEZE)
    source = authority.load(SOURCE)
    expected = {
        str(row["worker_id"]): dict(row) for row in freeze["workers"]
        if row.get("scene") == "hybench_xp_io"
    }
    rows = []
    for raw in source.get("workers") or []:
        worker_id = str(raw.get("worker_id") or "")
        if worker_id not in expected:
            continue
        row = copy.deepcopy(dict(raw))
        frozen = expected[worker_id]
        for name in (
            "database_cpuset", "database_cpuset_mems", "client_cpuset",
            "client_cpuset_mems", "cpu_quota_cores", "memory_bytes",
            "swap_bytes", "pids_max", "io_read_bps", "io_write_bps",
        ):
            if row.get(name) != frozen.get(name):
                raise RuntimeError(f"{worker_id} IO resource changed in combined freeze")
        inspected = reset4._docker_json("inspect", str(row["container"]))
        host = inspected.get("HostConfig") or {}
        if (
            inspected.get("Id") != row.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or str(host.get("CpusetCpus") or "") != row["database_cpuset"]
            or str(host.get("CpusetMems") or "") != row["database_cpuset_mems"]
        ):
            raise RuntimeError(f"{worker_id} live IO identity differs")
        rows.append(row)
    if source.get("status") != "PASS" or set(expected) != {
        str(row["worker_id"]) for row in rows
    }:
        raise RuntimeError("IO combined transition worker set differs")
    document = copy.deepcopy(source)
    document.update({
        "schema_version": "mysql_xp_io_combined_resource_transition_binding_v1",
        "freeze_path": str(FREEZE), "freeze_sha256": authority.sha(FREEZE),
        "workers": rows, "source_reset_binding_path": str(SOURCE),
        "source_reset_binding_sha256": authority.sha(SOURCE),
        "io_resource_envelope_changed": False,
        "sibling_cpu_resource_table_updated": True,
        "algorithm_state_or_candidate_credit_increment": 0,
    })
    authority.atomic(OUTPUT, document)
    print(json.dumps({"status": "PASS", "output": str(OUTPUT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

