#!/usr/bin/env python3
"""Capture the live CPU-worker slot transition as a reset-binding authority."""

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


FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v1"
    / "resource_successor.json"
)
SOURCE = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v188_to_b_top4_fresh_20260826_attempt012_xp_cpu/fresh_calibration"
    / "xp_resets/algorithm_b_calibration_05/reset_binding.json"
)
OUTPUT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v201_to_xp_cpu_disjoint_slot_transition_20260826/reset_binding.json"
)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh transition binding required: {OUTPUT}")
    freeze = authority.load(FREEZE)
    source = authority.load(SOURCE)
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("scene") == "hybench_xp_cpu"
    }
    rows = []
    readback = []
    for raw in source.get("workers") or []:
        worker_id = str(raw.get("worker_id") or "")
        if worker_id not in frozen:
            continue
        expected = frozen[worker_id]
        row = copy.deepcopy(dict(raw))
        for name in (
            "database_cpuset", "database_cpuset_mems", "client_cpuset",
            "client_cpuset_mems", "cpu_quota_cores", "memory_bytes",
            "swap_bytes", "pids_max", "io_read_bps", "io_write_bps",
        ):
            row[name] = expected[name]
        inspected = reset4._docker_json("inspect", str(row["container"]))
        host = inspected.get("HostConfig") or {}
        state = inspected.get("State") or {}
        if (
            inspected.get("Id") != row.get("container_id")
            or state.get("Running") is not True
            or str(host.get("CpusetCpus") or "") != row["database_cpuset"]
            or str(host.get("CpusetMems") or "") != row["database_cpuset_mems"]
            or int(host.get("NanoCpus") or 0)
            != int(round(float(row["cpu_quota_cores"]) * 1_000_000_000))
            or int(host.get("Memory") or 0) != int(row["memory_bytes"])
            or int(host.get("PidsLimit") or 0) != int(row["pids_max"])
        ):
            raise RuntimeError(f"{worker_id} live resource transition differs")
        defaults = Path(str(row["credential_defaults"]))
        sessions = reset4._host_mysql(
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'",
            timeout=30,
        ).stdout.strip()
        if sessions != "0":
            raise RuntimeError(f"{worker_id} has active benchmark sessions")
        row["slot_transition_readback"] = {
            "cpuset_cpus": host.get("CpusetCpus"),
            "cpuset_mems": host.get("CpusetMems"),
            "nano_cpus": host.get("NanoCpus"),
            "memory_bytes": host.get("Memory"),
            "memory_swap_bytes": host.get("MemorySwap"),
            "pids_limit": host.get("PidsLimit"),
            "container_pid": state.get("Pid"),
        }
        rows.append(row)
        readback.append({"worker_id": worker_id, **row["slot_transition_readback"]})
    if source.get("status") != "PASS" or set(frozen) != {
        str(row["worker_id"]) for row in rows
    }:
        raise RuntimeError("CPU transition source worker set differs")
    document = copy.deepcopy(source)
    document.update({
        "schema_version": "mysql_xp_cpu_disjoint_slot_transition_binding_v1",
        "status": "PASS",
        "freeze_path": str(FREEZE),
        "freeze_sha256": authority.sha(FREEZE),
        "workers": rows,
        "source_reset_binding_path": str(SOURCE),
        "source_reset_binding_sha256": authority.sha(SOURCE),
        "resource_envelope_transition": "database_cpu_and_numa_affinity_only",
        "algorithm_state_or_candidate_credit_increment": 0,
        "external_postgresql_control_or_stop_performed": False,
        "effective_resource_readback": readback,
    })
    authority.atomic(OUTPUT, document)
    print(json.dumps({
        "status": "PASS", "output": str(OUTPUT),
        "freeze_sha256": authority.sha(FREEZE),
        "workers": sorted(frozen),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

