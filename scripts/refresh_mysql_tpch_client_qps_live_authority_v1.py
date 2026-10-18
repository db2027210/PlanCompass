#!/usr/bin/env python3
"""Seal current TPC-H workers with the bounded metadata-only v24 gate."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as gate_v24
from scripts import mysql_safe_host_pressure_v1 as safe_pressure
from scripts import preflight_mysql_four_scene_live_v8 as preflight
from scripts import refresh_mysql_cpu_a_slots_memory_scene_authority_v1 as base


def main() -> int:
    # The v13 inventory reads /proc/PID/cmdline and can block in
    # access_remote_vm for the entire maximum wait.  v24 intentionally uses
    # status/comm/cgroup metadata and preserves the same fail-closed container,
    # CPU, NUMA, memory/swap, process, storage and D-state checks.
    base.implementation["dynamic_gate"] = gate_v24
    freeze = base.implementation["load"](base.implementation["FREEZE"])
    target_cpus = {
        cpu
        for row in freeze["workers"]
        if row["scene"] == "tpch_sf4_memory"
        for field in ("database_cpuset", "client_cpuset")
        for cpu in base.implementation["cpus"](str(row[field]))
    }
    safe_pressure.configure_target_cpus(target_cpus)
    preflight.host_pressure = safe_pressure.host_pressure
    result = int(base.main())
    output = Path(sys.argv[sys.argv.index("--output") + 1]).expanduser().resolve()
    live_path = output / "live_authority.json"
    live = base.implementation["load"](live_path)
    live["dynamic_gate_successor"] = {
        "source_path": str(Path(gate_v24.__file__).resolve()),
        "source_sha256": base.implementation["sha"](
            Path(gate_v24.__file__).resolve()
        ),
        "release_version": "v80",
        "proc_cmdline_reads_performed": 0,
        "status_comm_cgroup_metadata_only": True,
        "host_pressure_source_path": str(Path(safe_pressure.__file__).resolve()),
        "host_pressure_source_sha256": base.implementation["sha"](
            Path(safe_pressure.__file__).resolve()
        ),
        "proc_status_reads_performed": 0,
        "external_process_control_or_stop_performed": False,
    }
    base.implementation["atomic"](live_path, live)
    print(json.dumps(live, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
