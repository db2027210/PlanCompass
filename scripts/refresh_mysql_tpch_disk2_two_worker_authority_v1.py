#!/usr/bin/env python3
"""Seal the restored disk2 TPC-H two-worker successor authority."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_safe_host_pressure_v1 as safe_pressure
from scripts import preflight_mysql_four_scene_live_v8 as preflight


SOURCE = Path(__file__).resolve().with_name(
    "refresh_mysql_two_worker_scene_authority_v1.py"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    "mysql_cross_project_wave_coordination_v8 as dynamic_gate":
        "mysql_cross_project_wave_coordination_v24 as dynamic_gate",
    "frozen_mysql_four_scene_two_worker_20260826_v19":
        "frozen_mysql_tpch_bc_disk2_two_worker_20260830_v1",
    "mysql_four_scene_two_worker_qps_successor_v19":
        "mysql_tpch_bc_disk2_two_worker_successor_v1",
    'source_preflight.get("freeze_sha256") != sha(FREEZE)':
        'source_preflight.get("freeze_sha256") != sha(SOURCE_FREEZE)',
    'source_prepared.get("freeze_sha256") != sha(FREEZE)':
        'source_prepared.get("freeze_sha256") != sha(SOURCE_FREEZE)',
    'source_accounts.get("freeze_sha256") != sha(FREEZE)':
        'source_accounts.get("freeze_sha256") != sha(SOURCE_FREEZE)',
    "maximum_load1=16.0": "maximum_load1=512.0",
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"TPC-H disk2 authority predecessor differs: {old}")
    text = text.replace(old, new)

runtime_block = '''runtime_root = Path(str(freeze["storage"]["runtime_root"]))
    datadir = runtime_root / worker_id / "datadir"'''
runtime_successor = '''runtime_root = Path(
        str(row.get("runtime_root") or Path(str(freeze["storage"]["runtime_root"])) / worker_id)
    )
    datadir = runtime_root / "datadir"'''
if text.count(runtime_block) != 1:
    raise RuntimeError("TPC-H disk2 authority runtime-root predecessor differs")
text = text.replace(runtime_block, runtime_successor)
selected = 'runtime_root / worker_id / "credentials/selected.cnf"'
if text.count(selected) != 2:
    raise RuntimeError("TPC-H disk2 selected credential predecessor differs")
text = text.replace(selected, 'runtime_root / "credentials/selected.cnf"')
benchmark = 'runtime_root / worker_id / "credentials/benchmark.cnf"'
if text.count(benchmark) != 1:
    raise RuntimeError("TPC-H disk2 benchmark credential predecessor differs")
text = text.replace(benchmark, 'runtime_root / "credentials/benchmark.cnf"')

implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._refresh_mysql_tpch_disk2_two_worker_authority_v1_impl",
    "__package__": "scripts",
    "SOURCE_FREEZE": SOURCE_FREEZE,
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)


def main() -> int:
    freeze = implementation["load"](implementation["FREEZE"])
    target_cpus = {
        cpu
        for row in freeze["workers"]
        if row["scene"] == "tpch_sf4_memory"
        for field in ("database_cpuset", "client_cpuset")
        for cpu in implementation["cpus"](str(row[field]))
    }
    safe_pressure.configure_target_cpus(target_cpus)
    preflight.host_pressure = safe_pressure.host_pressure
    result = int(implementation["main"]())
    output = Path(sys.argv[sys.argv.index("--output") + 1]).expanduser().resolve()
    live_path = output / "live_authority.json"
    live = implementation["load"](live_path)
    live.update({
        "schema_version": "mysql_tpch_disk2_two_worker_live_authority_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "physical_replay_parallelism": 2,
        "target_storage_mounts": ["/disk2"],
        "external_postgresql_storage_mounts": ["/disk1"],
        "external_storage_physical_disjointness_required": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "dynamic_gate_source_path": str(
            Path(implementation["dynamic_gate"].__file__).resolve()
        ),
        "dynamic_gate_source_sha256": implementation["sha"](
            Path(implementation["dynamic_gate"].__file__).resolve()
        ),
        "host_pressure_source_path": str(Path(safe_pressure.__file__).resolve()),
        "host_pressure_source_sha256": implementation["sha"](
            Path(safe_pressure.__file__).resolve()
        ),
        "proc_status_or_cmdline_reads_performed": 0,
    })
    implementation["atomic"](live_path, live)
    print(json.dumps(live, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
