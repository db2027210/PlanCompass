#!/usr/bin/env python3
"""Build one live eight-worker Algorithm-A authority from terminal B reset evidence."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_scene_v1 as reset8


FREEZE = ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/xp_effective_resource_successor.json"
PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def parse_cpuset(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        if "-" in token:
            left, right = token.split("-", 1)
            result.update(range(int(left), int(right) + 1))
        elif token:
            result.add(int(token))
    return result


def prepare(scene: str, reset_binding: Path, output: Path) -> dict[str, Any]:
    freeze = load(FREEZE)
    binding = load(reset_binding)
    prefix = PREFIX[scene]
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in binding.get("workers") or []
        if row.get("worker_id") in expected
    }
    if binding.get("status") != "PASS" or set(rows) != expected:
        raise RuntimeError(f"{scene} terminal B reset binding differs")
    reset8.install_for_scene(rows)
    reset8.configure(freeze=FREEZE, prepared=reset_binding)
    for worker_id, row in rows.items():
        inspect = reset8._docker_json("inspect", str(row["container"]))
        if inspect.get("Id") != row.get("container_id") or (inspect.get("State") or {}).get("Running") is not True:
            raise RuntimeError(f"{worker_id} terminal B live identity differs")
        reset8.predecessor.base._impl["prep"]._kernel_resource_readback(inspect, row)
        reset8.predecessor.base._impl["prep"]._enforce_blkio(inspect, freeze, row)
    target_cpus = set().union(*(
        parse_cpuset(str(row["database_cpuset"])) | parse_cpuset(str(row["client_cpuset"]))
        for row in rows.values()
    ))
    rolling = gate_v17.rolling_quiet_gate(
        run_id=f"mysql-a-xp8-authority-{prefix}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=600.0,
        maximum_load1=512.0, target_cpus=target_cpus,
    )
    if rolling.get("status") != "PASS":
        raise RuntimeError(f"{scene} Algorithm A rolling gate failed closed")
    all_rows = []
    for frozen in freeze.get("workers") or []:
        worker_id = str(frozen["worker_id"])
        all_rows.append(copy.deepcopy(rows[worker_id]) if worker_id in rows else {
            **copy.deepcopy(dict(frozen)), "ready": False,
            "authority_placeholder_for_other_scene": True,
        })
    accepted_rounds = {name: 0 for name in (
        "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
    )}
    common = {
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority_scene_scope": scene,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "accepted_rounds": accepted_rounds,
        "observed_seconds": float(rolling.get("observed_seconds") or 0.0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": rolling,
        "recalibration_performed": False,
        "accepted_state_credit_increment": 0,
        "external_process_control_or_stop_performed": False,
    }
    preflight = output / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_algorithm_a_xp8_live_preflight_v1"})
    prepared = output / "prepared_binding.json"
    atomic(prepared, {
        **common, "status": "PREPARED",
        "schema_version": "mysql_algorithm_a_xp8_live_prepared_v1",
        "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
        "workers": all_rows, "workload_started": False,
        "fresh_r0_complete": False, "formal_outer_started": False,
    })
    accounts = output / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_algorithm_a_xp8_live_accounts_v1",
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": sha(prepared),
        "workers": [{**copy.deepcopy(row), "passwords_emitted": False} for row in all_rows],
        "passwords_emitted": False, "workload_started": False,
    })
    live = output / "live_authority.json"
    atomic(live, {
        "schema_version": "mysql_algorithm_a_xp8_live_authority_v1",
        "status": "PASS", "created_at": now(), "scene": scene,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "physical_replay_parallelism": 8,
        "recalibration_performed": False, "accepted_state_credit_increment": 0,
        "resource_freeze_path": str(FREEZE), "resource_freeze_sha256": sha(FREEZE),
        "source_reset_binding_path": str(reset_binding),
        "source_reset_binding_sha256": sha(reset_binding),
        "artifacts": {"preflight": str(preflight), "prepared": str(prepared), "accounts": str(accounts)},
    })
    return {"status": "PASS", "authority": str(live), "workers": sorted(rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=sorted(PREFIX), required=True)
    parser.add_argument("--reset-binding", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.scene, Path(args.reset_binding).resolve(), Path(args.output).resolve()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
