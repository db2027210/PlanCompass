#!/usr/bin/env python3
"""Seal one current XP8 authority from terminal C w1-w4 plus pre-provisioned w5-w8."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v5 as reset8


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
    / "xp_effective_resource_successor.json"
)
PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        if "-" in token:
            left, right = token.split("-", 1)
            result.update(range(int(left), int(right) + 1))
        elif token:
            result.add(int(token))
    return result


def terminal(state: Mapping[str, Any], scene: str) -> bool:
    converged = state.get("converged") or {}
    return bool(
        state.get("algorithm") == "C"
        and state.get("async_scene") == scene
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "FINISHED"
        and not state.get("error")
        and isinstance(converged, Mapping)
        and converged.get(scene) is True
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(PREFIX), required=True)
    parser.add_argument("--c-state", type=Path, required=True)
    parser.add_argument("--additions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scene = args.scene
    prefix = PREFIX[scene]
    state_path = args.c_state.resolve()
    additions_path = args.additions.resolve()
    output = args.output.resolve()
    if HISTORY.resolve() not in output.parents:
        raise RuntimeError("authority output beneath formal history required")
    manifest_path = output / "expansion_manifest.json"
    if manifest_path.is_file() and load(manifest_path).get("status") == "PASS":
        print(json.dumps({"status": "PASS", "manifest": str(manifest_path)}, sort_keys=True))
        return 0
    if output.exists() or output.is_symlink():
        raise RuntimeError("fresh XP8 authority output required")
    state = load(state_path)
    additions_document = load(additions_path)
    freeze = load(FREEZE)
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
    }
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    expected_current = {f"{prefix}_w{i}" for i in range(1, 5)}
    expected_additions = {f"{prefix}_w{i}" for i in range(5, 9)}
    additions = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in additions_document.get("workers") or []
        if row.get("worker_id") in expected_additions
    }
    if (
        not terminal(state, scene)
        or freeze.get("schema_version") != "mysql_xp_eight_worker_cpu_a_slots_successor_v6"
        or additions_document.get("status") != "PASS"
        or additions_document.get("freeze_sha256") != sha(FREEZE)
        or additions_document.get("algorithm_a_candidate_injection") is not False
        or additions_document.get("algorithm_a_qps_used_for_generation_or_ranking") is not False
        or additions_document.get("algorithm_a_parameter_ranges_used") is not False
        or set(additions) != expected_additions
    ):
        raise RuntimeError(f"{scene} terminal/addition authority differs")
    reset8.install_for_workers(frozen)
    impl = reset8.base._impl
    evidence_rows: dict[str, list[dict[str, Any]]] = {key: [] for key in expected_current}
    for binding_path in sorted(state_path.parent.glob("xp_resets/**/reset_binding.json")):
        try:
            document = load(binding_path)
        except (OSError, json.JSONDecodeError, RuntimeError):
            continue
        if document.get("status") != "PASS":
            continue
        for row in document.get("workers") or []:
            worker_id = str(row.get("worker_id") or "")
            if worker_id in evidence_rows:
                evidence_rows[worker_id].append(copy.deepcopy(dict(row)))
    live: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(expected):
        worker = frozen[worker_id]
        inspected = impl["_docker_json"]("inspect", str(worker["container"]))
        state_row = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        host = inspected.get("HostConfig") or {}
        mounts = {
            str(row.get("Destination") or ""): str(row.get("Source") or "")
            for row in inspected.get("Mounts") or []
        }
        if state_row.get("Running") is not True:
            raise RuntimeError(f"{worker_id} current container is not running")
        if (
            labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or labels.get("ra.scenario") != scene
            or str(host.get("CpusetCpus") or "") != str(worker["database_cpuset"])
            or mounts.get("/var/lib/mysql") != str(worker["runtime_root"] + "/datadir")
            or not mounts.get("/run/ra-credentials")
        ):
            raise RuntimeError(f"{worker_id} current container identity differs")
        if worker_id in additions:
            row = additions[worker_id]
            if row.get("container_id") != inspected.get("Id"):
                raise RuntimeError(f"{worker_id} addition container changed")
        else:
            matching = [
                row for row in evidence_rows[worker_id]
                if row.get("container_id") == inspected.get("Id")
            ]
            row = matching[-1] if matching else copy.deepcopy(worker)
        credential_root = Path(mounts["/run/ra-credentials"])
        defaults = credential_root / "selected.cnf"
        benchmark = credential_root / "benchmark.cnf"
        sessions = impl["_host_mysql"](
            worker, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = impl["_host_mysql"](
            worker, benchmark,
            "SELECT CURRENT_USER(),DATABASE(),"
            "(SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x');", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or len(identity) != 3 or not identity[0].startswith("ra_benchmark@") or identity[2] != "8":
            raise RuntimeError(f"{worker_id} current benchmark identity differs")
        row.update({
            **worker, "ready": True,
            "container_id": str(inspected["Id"]),
            "container_pid": int(state_row.get("Pid") or 0),
            "datadir": mounts["/var/lib/mysql"],
            "credential_defaults": str(defaults),
            "benchmark_defaults": str(benchmark),
            "benchmark_user": "ra_benchmark",
            "external_current_user": identity[0],
            "external_database": "hybench_sf10x",
            "database": "hybench_sf10x", "table_count": 8,
            "kernel_cgroup_resources": impl["prep"]._kernel_resource_readback(
                inspected, worker
            ),
            "blkio_authority": impl["prep"]._enforce_blkio(
                inspected, freeze, worker
            ),
            "passwords_emitted": False,
        })
        live[worker_id] = row
    target_cpus = set().union(*(
        cpus(str(row["database_cpuset"])) | cpus(str(row["client_cpuset"]))
        for row in live.values()
    ))
    rolling = gate_v17.rolling_quiet_gate(
        run_id=f"mysql-b-xp8-terminal-{prefix}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=600.0,
        maximum_load1=512.0, target_cpus=target_cpus,
    )
    if rolling.get("status") != "PASS":
        raise RuntimeError(f"{scene} XP8 terminal rolling gate failed")
    all_rows = []
    for worker in freeze.get("workers") or []:
        worker_id = str(worker["worker_id"])
        all_rows.append(copy.deepcopy(live[worker_id]) if worker_id in live else {
            **copy.deepcopy(dict(worker)), "ready": False,
            "authority_placeholder_for_other_scene": True,
        })
    common = {
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority_scene_scope": scene,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": rolling,
        "observed_seconds": float(rolling.get("observed_seconds") or 0),
        "external_process_control_or_stop_performed": False,
        "algorithm_c_terminal_sequence_gate_path": str(state_path),
        "algorithm_c_terminal_sequence_gate_sha256": sha(state_path),
        "algorithm_c_candidate_or_state_credit_used_by_b": False,
        "algorithm_a_candidate_injection": False,
        "algorithm_a_qps_used_for_generation_or_ranking": False,
        "algorithm_a_parameter_ranges_used": False,
        "algorithm_a_role": "independent_posthoc_check_only",
    }
    preflight = output / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_b_xp8_terminal_preflight_v1"})
    prepared = output / "prepared_binding.json"
    atomic(prepared, {
        **common, "status": "PREPARED",
        "schema_version": "mysql_b_xp8_terminal_prepared_v1",
        "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
        "workers": all_rows, "workload_started": False,
        "fresh_r0_complete": False, "formal_outer_started": False,
    })
    accounts = output / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_b_xp8_terminal_accounts_v1",
        "prepared_binding_path": str(prepared),
        "prepared_binding_sha256": sha(prepared),
        "workers": [{**copy.deepcopy(row), "passwords_emitted": False} for row in all_rows],
        "passwords_emitted": False, "workload_started": False,
    })
    atomic(manifest_path, {
        **common, "schema_version": "mysql_b_xp8_terminal_expansion_v1",
        "scene": scene, "worker_ids": sorted(live),
        "physical_replay_parallelism": 8,
        "new_worker_ids": sorted(expected_additions),
        "source_additions_binding_path": str(additions_path),
        "source_additions_binding_sha256": sha(additions_path),
        "preflight_path": str(preflight), "prepared_binding_path": str(prepared),
        "benchmark_accounts_path": str(accounts),
    })
    print(json.dumps({"status": "PASS", "manifest": str(manifest_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
