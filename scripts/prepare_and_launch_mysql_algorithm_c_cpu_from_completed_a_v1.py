#!/usr/bin/env python3
"""Reset the two completed CPU-A lanes and launch fresh top4 CPU-C v74.

This transition is scene-local.  It does not inspect, stop, or mutate the
running XP-I/O cohort and it carries no Algorithm-A measurement credit into C.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v11 as gate
from scripts import reset_mysql_xp_data_planes_v8 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
CPU_A_ROOT = HISTORY / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu"
CPU_A_STATE = CPU_A_ROOT / "algorithm_a_state.json"
CPU_A_BINDING = (
    CPU_A_ROOT / "round_010/wave_002/xp_resets"
    / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu_round_010_wave_002_attempt_01"
    / "reset_binding.json"
)
SOURCE_AUTHORITY = HISTORY / "v301_to_four_scene_two_worker_live_authority_20260826"
CHECKPOINT = SOURCE_AUTHORITY / "checkpoint/prepared_binding.json"
QUARANTINE = SOURCE_AUTHORITY / "replay_quarantine/replay_quarantine.json"
RESET_RUN_ID = "v860_to_c_cpu_from_completed_a_fresh_reset_20260828"
RESET_ROOT = HISTORY / RESET_RUN_ID
AUTHORITY_ROOT = HISTORY / "v861_to_c_cpu_from_completed_a_live_authority_20260828"
C_CAMPAIGN = "v862_to_c_cpu_global_monotonic_fresh_20260828_attempt001"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v74.py"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v74.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_cpu_c_from_completed_a_20260828_v1"
    / "transition_contract.json"
)
SCENE = "hybench_xp_cpu"
TARGET_IDS = {"xp_cpu_w1", "xp_cpu_w2"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


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
    for segment in value.split(","):
        start, separator, end = segment.strip().partition("-")
        result.update(range(int(start), int(end) + 1) if separator else [int(start)])
    return result


def validate() -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    contract = load(CONTRACT)
    state = load(CPU_A_STATE)
    binding = load(CPU_A_BINDING)
    freeze = load(FREEZE)
    frozen = {str(row["worker_id"]): dict(row) for row in freeze.get("workers") or []}
    prior = {
        str(row["worker_id"]): dict(row)
        for row in binding.get("workers") or []
        if str(row.get("worker_id")) in TARGET_IDS
    }
    expected_bindings = {
        "transition_controller": Path(__file__).resolve(),
        "algorithm_c_launcher": LAUNCHER,
        "algorithm_c_controller": CONTROLLER,
        "resource_freeze": FREEZE,
        "source_algorithm_a_state": CPU_A_STATE,
        "source_algorithm_a_reset_binding": CPU_A_BINDING,
        "source_authority_checkpoint": CHECKPOINT,
        "source_replay_quarantine": QUARANTINE,
    }
    if (
        contract.get("schema_version")
        != "mysql_cpu_c_from_completed_a_transition_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != SCENE
        or contract.get("physical_workers") != 2
        or contract.get("algorithm_a_measurement_credit_reused") is not False
        or contract.get("external_process_control_or_stop_performed") is not False
        or state.get("status") != "COMPLETE"
        or state.get("stage") != "COMPLETE"
        or int(state.get("round_index") or 0) != 10
        or binding.get("status") != "PASS"
        or freeze.get("schema_version")
        != "mysql_four_scene_two_worker_qps_successor_v19"
        or set(frozen) != {
            "job_w1", "job_w2", "tpch_w1", "tpch_w2",
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        }
        or set(prior) != TARGET_IDS
        or any(frozen[key]["scene"] != SCENE for key in TARGET_IDS)
    ):
        raise RuntimeError("completed CPU-A to CPU-C transition boundary differs")
    for label, path in expected_bindings.items():
        row = dict((contract.get("source_bindings") or {}).get(label) or {})
        if (
            row.get("path") != str(path.relative_to(ROOT))
            or row.get("sha256") != sha(path)
        ):
            raise RuntimeError(f"CPU-C transition contract binding differs: {label}")
    for worker_id in sorted(TARGET_IDS):
        inspected = reset._v6._docker_json(
            "inspect", str(frozen[worker_id]["container"])
        )
        if (
            inspected.get("Id") != prior[worker_id].get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or (inspected.get("Config") or {}).get("Labels", {}).get("ra.owner")
            != "mysql_four_scene_v5"
        ):
            raise RuntimeError(f"{worker_id} completed CPU-A live identity differs")
    return freeze, prior, state


def reset_workers(
    freeze: dict[str, Any], prior: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if RESET_ROOT.exists() or RESET_ROOT.is_symlink():
        raise RuntimeError(f"fresh reset output required: {RESET_ROOT}")
    RESET_ROOT.mkdir(parents=True, mode=0o700)
    quarantine = Path(str(freeze["storage"]["runtime_root"])) / "quarantine" / RESET_RUN_ID
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="cpu-c-reset") as pool:
        futures = {
            pool.submit(
                reset._v6._prepare_fresh_xp,
                freeze, frozen[worker_id], prior[worker_id], quarantine,
                RESET_ROOT, False, "formal_point", False, False, False, False,
            ): worker_id
            for worker_id in sorted(TARGET_IDS)
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            results[worker_id] = future.result()
            print(json.dumps({
                "stage": "RESET_READY", "worker_id": worker_id,
                "container_id": results[worker_id]["container_id"],
                "copy_seconds": results[worker_id]["copy_seconds"],
            }, sort_keys=True), flush=True)
    reset_binding = {
        "schema_version": "mysql_completed_cpu_a_to_cpu_c_reset_binding_v1",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True, "scene": SCENE,
        "source_algorithm_a_state": str(CPU_A_STATE),
        "source_algorithm_a_state_sha256": sha(CPU_A_STATE),
        "source_algorithm_a_binding": str(CPU_A_BINDING),
        "source_algorithm_a_binding_sha256": sha(CPU_A_BINDING),
        "algorithm_a_measurement_credit_reused": False,
        "accepted_round_credit_increment": 0,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "workers": [results[key] for key in sorted(results)],
    }
    atomic(RESET_ROOT / "reset_binding.json", reset_binding)
    return results


def build_authority(
    freeze: dict[str, Any], reset_rows: dict[str, dict[str, Any]],
) -> dict[str, Path]:
    if AUTHORITY_ROOT.exists() or AUTHORITY_ROOT.is_symlink():
        raise RuntimeError(f"fresh authority output required: {AUTHORITY_ROOT}")
    source_preflight = load(SOURCE_AUTHORITY / "transition_preflight.json")
    source_prepared = load(SOURCE_AUTHORITY / "prepared_binding.json")
    source_accounts = load(SOURCE_AUTHORITY / "benchmark_accounts_binding.json")
    prepared_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source_prepared.get("workers") or []
    }
    account_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in source_accounts.get("workers") or []
    }
    if (
        source_preflight.get("status") != "PASS"
        or source_prepared.get("status") != "PREPARED"
        or source_accounts.get("status") != "PASS"
        or set(prepared_rows) != set(account_rows)
        or len(prepared_rows) != 8
    ):
        raise RuntimeError("source v301 live-authority lineage differs")
    for worker_id, row in reset_rows.items():
        adopted = copy.deepcopy(row)
        adopted.update({
            "benchmark_user": "ra_benchmark", "database": "hybench_sf10x",
            "external_database": "hybench_sf10x",
            "ready": True, "passwords_emitted": False,
        })
        prepared_rows[worker_id] = copy.deepcopy(adopted)
        account_rows[worker_id] = copy.deepcopy(adopted)
    target_cpus = {
        cpu for worker_id in TARGET_IDS
        for field in ("database_cpuset", "client_cpuset")
        for cpu in cpus(str(freeze_worker(freeze, worker_id)[field]))
    }
    dynamic = gate.rolling_quiet_gate(
        run_id="mysql-cpu-c-v861-authority", required_seconds=60.0,
        maximum_wait_seconds=180.0, maximum_load1=512.0,
        target_cpus=target_cpus,
    )
    if dynamic.get("status") != "PASS":
        raise RuntimeError(f"CPU-C dynamic non-interference gate failed: {dynamic.get('status')}")
    AUTHORITY_ROOT.mkdir(parents=True, mode=0o700)
    accepted = {
        "job_memory": 0, "tpch_sf4_memory": 0,
        "hybench_xp_cpu": 0, "hybench_xp_io": 0,
    }
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": SCENE, "freeze_path": str(FREEZE),
        "freeze_sha256": sha(FREEZE), "accepted_rounds": accepted,
        "observed_seconds": float(dynamic.get("observed_seconds") or 60.0),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": dynamic,
        "source_authority_path": str(SOURCE_AUTHORITY),
        "source_reset_binding": str(RESET_ROOT / "reset_binding.json"),
        "source_reset_binding_sha256": sha(RESET_ROOT / "reset_binding.json"),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
        "algorithm_a_measurement_credit_reused": False,
    }
    preflight_path = AUTHORITY_ROOT / "transition_preflight.json"
    atomic(preflight_path, {
        **common,
        "schema_version": "mysql_cpu_c_scene_transition_preflight_v1",
        "status": "PASS", "blockers": [], "launch_performed": False,
    })
    prepared_path = AUTHORITY_ROOT / "prepared_binding.json"
    atomic(prepared_path, {
        **common,
        "schema_version": "mysql_cpu_c_scene_prepared_binding_v1",
        "status": "PREPARED", "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "workers": [prepared_rows[key] for key in sorted(prepared_rows)],
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts_path = AUTHORITY_ROOT / "benchmark_accounts_binding.json"
    atomic(accounts_path, {
        **common,
        "schema_version": "mysql_cpu_c_scene_accounts_binding_v1",
        "status": "PASS", "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": [account_rows[key] for key in sorted(account_rows)],
        "passwords_emitted": False, "workload_started": False,
    })
    r0_path = AUTHORITY_ROOT / "fresh_r0_binding.json"
    atomic(r0_path, {
        **common,
        "schema_version": "mysql_cpu_c_scene_fresh_r0_authority_v1",
        "status": "PASS", "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "fresh_r0_complete": True, "formal_outer_launch_authorized": True,
        "formal_outer_started": False, "recalibration_performed": False,
        "target_bottleneck_gate": {
            "passed": True,
            "fresh_incumbent_measurement_owned_by_controller": True,
        },
        "failures": [], "client_isolation_failures": [],
    })
    return {
        "preflight": preflight_path, "prepared": prepared_path,
        "accounts": accounts_path, "r0": r0_path,
    }


def freeze_worker(freeze: Mapping[str, Any], worker_id: str) -> dict[str, Any]:
    rows = [dict(row) for row in freeze.get("workers") or [] if row.get("worker_id") == worker_id]
    if len(rows) != 1:
        raise RuntimeError(f"frozen worker cardinality differs: {worker_id}")
    return rows[0]


def main() -> int:
    freeze, prior, _state = validate()
    rows = reset_workers(freeze, prior)
    authority = build_authority(freeze, rows)
    command = [
        str(Path("/home/anonymous/miniconda3/envs/py313/bin/python")),
        str(LAUNCHER), "--run-id", C_CAMPAIGN,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(authority["preflight"]),
        "--prepared-binding", str(authority["prepared"]),
        "--benchmark-accounts", str(authority["accounts"]),
        "--fresh-r0", str(authority["r0"]),
        "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--scene", SCENE,
    ]
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=1800,
    )
    launch = {
        "schema_version": "mysql_completed_cpu_a_to_cpu_c_launch_v1",
        "created_at": now(), "status": "LAUNCHED" if completed.returncode == 0 else "FAILED_CLOSED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "scene": SCENE, "campaign": C_CAMPAIGN, "command": command,
        "returncode": completed.returncode, "stdout": completed.stdout,
        "stderr": completed.stderr, "external_process_control_or_stop_performed": False,
    }
    atomic(AUTHORITY_ROOT / "launch_result.json", launch)
    print(json.dumps(launch, ensure_ascii=False, sort_keys=True), flush=True)
    if completed.returncode != 0:
        raise RuntimeError(f"CPU-C launch failed: {completed.stderr[-2000:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
