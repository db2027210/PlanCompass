#!/usr/bin/env python3
"""Persist a bounded TPC-H dynamic non-interference gate diagnostic."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v24 as gate
from scripts import mysql_safe_host_pressure_v1 as safe_pressure
from scripts import preflight_mysql_four_scene_live_v8 as preflight


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
SCENE = "tpch_sf4_memory"


def _cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        lower, separator, upper = token.strip().partition("-")
        result.update(
            range(int(lower), int(upper) + 1) if separator else [int(lower)]
        )
    return result


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{_sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--required-seconds", type=float, default=15.0)
    parser.add_argument("--maximum-wait-seconds", type=float, default=90.0)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if HISTORY.resolve() not in output.parents or output.exists():
        raise RuntimeError("fresh diagnostic output under formal history required")
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    workers = [row for row in freeze["workers"] if row["scene"] == SCENE]
    target_cpus = {
        cpu
        for row in workers
        for field in ("database_cpuset", "client_cpuset")
        for cpu in _cpus(str(row[field]))
    }
    safe_pressure.configure_target_cpus(target_cpus)
    preflight.host_pressure = safe_pressure.host_pressure
    result = gate.rolling_quiet_gate(
        run_id=f"mysql-tpch-gate-diagnostic-{os.getpid()}",
        required_seconds=args.required_seconds,
        maximum_wait_seconds=args.maximum_wait_seconds,
        maximum_load1=512.0,
        target_cpus=target_cpus,
    )
    document: dict[str, object] = {
        "schema_version": "mysql_tpch_dynamic_gate_diagnostic_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "scene": SCENE,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": _sha(FREEZE),
        "gate_source_path": str(Path(gate.__file__).resolve()),
        "gate_source_sha256": _sha(Path(gate.__file__).resolve()),
        "external_process_control_or_stop_performed": False,
        "gate": result,
    }
    _atomic(output / "dynamic_gate_diagnostic.json", document)
    print(json.dumps({
        "status": result.get("status"),
        "passed": result.get("passed"),
        "errors": result.get("errors"),
        "observed_seconds": result.get("observed_seconds"),
        "output": str(output / "dynamic_gate_diagnostic.json"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
