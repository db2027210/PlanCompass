#!/usr/bin/env python3
"""Prepare the six remaining CPU-A slots for JOB, TPC-H, and XP-I/O C."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v8 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
CPU_A_ROOT = HISTORY / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu"
CPU_A_BINDING = (
    CPU_A_ROOT / "round_010/wave_002/xp_resets"
    / "v843_to_a_cpu_round5_control2_resume_20260827_xp_cpu_round_010_wave_002_attempt_01"
    / "reset_binding.json"
)
CPU_AUTHORITY = HISTORY / "v861_to_c_cpu_from_completed_a_live_authority_20260828"
OUTPUT = HISTORY / "v864_to_c_four_scene_cpu_a_slots_prepared_20260828"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_prepare_20260828_v1"
    / "transition_contract.json"
)
TARGET_TO_PRIOR = {
    "job_w1": "xp_cpu_w5", "job_w2": "xp_cpu_w7",
    "tpch_w1": "xp_cpu_w6", "tpch_w2": "xp_cpu_w8",
    "xp_io_w1": "xp_cpu_w3", "xp_io_w2": "xp_cpu_w4",
}
DATABASES = {
    "job_memory": "imdb_job", "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "hybench_sf10x", "hybench_xp_io": "hybench_sf10x",
}


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


def transformed_prepare_worker():
    source = ROOT / "scripts/prepare_mysql_four_scene_v4.py"
    text = source.read_text(encoding="utf-8")
    old = (
        'if not str(template).startswith("/disk2/") or not '
        'str(runtime_root).startswith("/disk2/"):'
    )
    new = (
        'if not str(template).startswith("/disk2/") or not '
        'str(runtime_root).startswith(str(Path(freeze["storage"]["mount"]).resolve()) + "/"):'
    )
    if text.count(old) != 1:
        raise RuntimeError("preparation path guard predecessor differs")
    namespace: dict[str, Any] = {
        "__file__": str(source), "__name__": "scripts._prepare_cpu_a_slots_impl",
        "__package__": "scripts",
    }
    exec(compile(text.replace(old, new), str(source), "exec"), namespace)
    return namespace["_prepare_worker"], namespace


PREPARE_WORKER, PREPARE_IMPL = transformed_prepare_worker()


def validate() -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    contract = load(CONTRACT)
    freeze = load(FREEZE)
    binding = load(CPU_A_BINDING)
    cpu_prepared = load(CPU_AUTHORITY / "prepared_binding.json")
    prior = {str(row["worker_id"]): dict(row) for row in binding.get("workers") or []}
    workers = [dict(row) for row in freeze.get("workers") or []]
    expected = {
        "controller": Path(__file__).resolve(), "resource_freeze": FREEZE,
        "completed_cpu_a_binding": CPU_A_BINDING,
        "cpu_c_prepared_binding": CPU_AUTHORITY / "prepared_binding.json",
        "preparation_predecessor": ROOT / "scripts/prepare_mysql_four_scene_v4.py",
    }
    if (
        contract.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_prepare_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("physical_prepare_parallelism") != 6
        or contract.get("external_process_control_or_stop_performed") is not False
        or freeze.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_two_worker_successor_v2"
        or binding.get("status") != "PASS"
        or cpu_prepared.get("status") != "PREPARED"
        or len(workers) != 8
        or not set(TARGET_TO_PRIOR).issubset({row["worker_id"] for row in workers})
    ):
        raise RuntimeError("CPU-A slot preparation boundary differs")
    for label, path in expected.items():
        row = dict((contract.get("source_bindings") or {}).get(label) or {})
        if row.get("path") != str(path.relative_to(ROOT)) or row.get("sha256") != sha(path):
            raise RuntimeError(f"CPU-A slot preparation binding differs: {label}")
    return freeze, prior, workers


def stop_and_discard(prior: Mapping[str, Any], target: Mapping[str, Any]) -> Path:
    container = str(target["container"])
    inspected = reset._v6._docker_json("inspect", container)
    if (
        inspected.get("Id") != prior.get("container_id")
        or (inspected.get("State") or {}).get("Running") is not True
        or str(prior.get("container")) != container
    ):
        raise RuntimeError(f"{target['worker_id']} completed CPU-A donor identity differs")
    reset._v6._stop_mysql_container(
        container, worker=dict(prior),
        defaults=Path(str(prior["credential_defaults"])),
        allow_failed_workload_nonzero_exit=False,
    )
    reset._v6._docker_run(["docker", "rm", container], timeout=120)
    old_root = Path(str(prior["runtime_root"]))
    quarantine = old_root.parent / "quarantine" / OUTPUT.name / str(target["worker_id"])
    if not old_root.is_dir() or quarantine.exists() or quarantine.is_symlink():
        raise RuntimeError(f"{target['worker_id']} donor runtime boundary differs")
    quarantine.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.rename(old_root, quarantine)
    return quarantine


def provision(row: dict[str, Any]) -> dict[str, Any]:
    password = secrets.token_hex(32)
    defaults = Path(str(row["runtime_root"])) / "credentials/benchmark.cnf"
    defaults.write_text(
        "[client]\nuser=ra_benchmark\npassword=" + password
        + "\nprotocol=tcp\nssl-mode=DISABLED\nget-server-public-key=TRUE\n",
        encoding="utf-8",
    )
    defaults.chmod(0o600)
    database = DATABASES[str(row["scene"])]
    root = str(row["credential_defaults"])
    sql = (
        "CREATE USER IF NOT EXISTS 'ra_benchmark'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}'; "
        "ALTER USER 'ra_benchmark'@'%' IDENTIFIED WITH "
        f"caching_sha2_password BY '{password}'; "
        f"GRANT ALL PRIVILEGES ON `{database}`.* TO 'ra_benchmark'@'%'; FLUSH PRIVILEGES;"
    )
    subprocess.run([
        "mysql", f"--defaults-file={root}", "--host=127.0.0.1",
        f"--port={row['host_port']}", "--connect-timeout=5", "-NBe", sql,
    ], cwd=ROOT, text=True, capture_output=True, check=True, timeout=60)
    identity = subprocess.run([
        "mysql", f"--defaults-file={defaults}", "--host=127.0.0.1",
        f"--port={row['host_port']}", "--connect-timeout=5", "-NBe",
        "SELECT CURRENT_USER(),DATABASE()", database,
    ], cwd=ROOT, text=True, capture_output=True, check=True, timeout=60).stdout.strip().split("\t")
    if len(identity) != 2 or not identity[0].startswith("ra_benchmark@") or identity[1] != database:
        raise RuntimeError(f"{row['worker_id']} benchmark account readback differs")
    row.update({
        "benchmark_defaults": str(defaults), "benchmark_user": "ra_benchmark",
        "database": database, "external_current_user": identity[0],
        "external_database": identity[1], "passwords_emitted": False,
    })
    return row


def prepare_one(
    freeze: Mapping[str, Any], target: dict[str, Any], prior: dict[str, Any],
) -> dict[str, Any]:
    quarantine = stop_and_discard(prior, target)
    storage = copy.deepcopy(dict((freeze.get("storage_by_worker") or {})[target["worker_id"]]))
    runtime = Path(str(target["runtime_root"]))
    if Path(str(storage["runtime_root"])).resolve() != runtime.parent.resolve():
        raise RuntimeError(f"{target['worker_id']} target storage root differs")
    mini = copy.deepcopy(dict(freeze))
    mini["storage"] = storage
    mini["container_owner_label"] = "mysql_four_scene_v5"
    row = PREPARE_WORKER(mini, target, OUTPUT / "workers")
    row["discarded_completed_cpu_a_runtime_root"] = str(quarantine)
    row["algorithm_a_measurement_credit_reused"] = False
    return provision(row)


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh preparation output required: {OUTPUT}")
    freeze, prior, workers = validate()
    OUTPUT.mkdir(parents=True, mode=0o700)
    targets = {row["worker_id"]: row for row in workers if row["worker_id"] in TARGET_TO_PRIOR}
    results: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="c-slots-prepare") as pool:
        futures = {
            pool.submit(prepare_one, freeze, target, prior[TARGET_TO_PRIOR[worker_id]]): worker_id
            for worker_id, target in sorted(targets.items())
        }
        for future in as_completed(futures):
            worker_id = futures[future]
            try:
                results[worker_id] = future.result()
                print(json.dumps({
                    "stage": "PREPARED", "worker_id": worker_id,
                    "container_id": results[worker_id]["container_id"],
                    "copy_seconds": results[worker_id]["copy_seconds"],
                }, sort_keys=True), flush=True)
            except BaseException as error:
                failures.append({"worker_id": worker_id, "error": f"{type(error).__name__}: {error}"})
                print(json.dumps({"stage": "FAILED_CLOSED", **failures[-1]}, sort_keys=True), flush=True)
    cpu_rows = {
        str(row["worker_id"]): dict(row)
        for row in load(CPU_AUTHORITY / "prepared_binding.json").get("workers") or []
        if str(row.get("worker_id")) in {"xp_cpu_w1", "xp_cpu_w2"}
    }
    frozen = {str(row["worker_id"]): dict(row) for row in workers}
    for worker_id, row in cpu_rows.items():
        results[worker_id] = {**frozen[worker_id], **row}
    status = "PREPARED" if not failures and set(results) == set(frozen) else "PREPARATION_FAILED_CLOSED"
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "accepted_rounds": {scene: 0 for scene in DATABASES},
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
        "algorithm_a_measurement_credit_reused": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    prepared_path = OUTPUT / "prepared_binding.json"
    atomic(prepared_path, {
        **common, "schema_version": "mysql_cpu_a_slots_prepared_data_planes_v1",
        "status": status, "workers": [results[key] for key in sorted(results)],
        "failures": failures, "physical_prepare_parallelism": 6,
    })
    accounts_path = OUTPUT / "benchmark_accounts_binding.json"
    atomic(accounts_path, {
        **common, "schema_version": "mysql_cpu_a_slots_benchmark_accounts_v1",
        "status": "PASS" if status == "PREPARED" else "FAILED_CLOSED",
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": [results[key] for key in sorted(results)],
        "failures": failures, "passwords_emitted": False,
    })
    print(json.dumps({
        "status": status, "prepared": len(results), "failures": failures,
        "prepared_binding": str(prepared_path), "accounts": str(accounts_path),
    }, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if status == "PREPARED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
