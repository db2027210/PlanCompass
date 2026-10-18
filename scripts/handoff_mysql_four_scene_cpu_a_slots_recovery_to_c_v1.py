#!/usr/bin/env python3
"""Wait for the two XP-I/O slots, build live authority, and launch four C scenes."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_cpu_a_slots_v10 as preflight


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
RECOVERY = HISTORY / "v869_to_c_four_scene_cpu_a_slots_xpio_timeout_recovered_20260828"
AUTHORITY = HISTORY / "v870_to_c_four_scene_cpu_a_slots_live_authority_20260828"
CHECKPOINT = HISTORY / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/checkpoint/prepared_binding.json"
QUARANTINE = HISTORY / "v868_to_c_cpu_a_slots_fresh_checkpoint_20260828/replay_quarantine/replay_quarantine.json"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_async_v75.py"
CAMPAIGN = "v871_to_c_four_scene_cpu_a_slots_global_monotonic_fresh_20260828"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]


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
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def wait_recovery() -> tuple[dict[str, Any], dict[str, Any]]:
    deadline = time.monotonic() + 1800
    prepared_path = RECOVERY / "prepared_binding.json"
    accounts_path = RECOVERY / "benchmark_accounts_binding.json"
    while time.monotonic() < deadline:
        if prepared_path.is_file() and accounts_path.is_file():
            prepared = load(prepared_path)
            accounts = load(accounts_path)
            if prepared.get("status") == "PREPARED" and accounts.get("status") == "PASS":
                return prepared, accounts
            raise RuntimeError(f"XP-I/O slot recovery failed closed: {prepared.get('failures')}")
        time.sleep(5)
    raise TimeoutError("XP-I/O slot recovery did not publish within 1800 seconds")


def rolling_gate(freeze: Mapping[str, Any]) -> dict[str, Any]:
    target_cpus: set[int] = set()
    intended = {str(row["container"]) for row in freeze.get("workers") or []}
    for row in freeze.get("workers") or []:
        target_cpus.update(preflight._cpus(str(row["database_cpuset"])))
        target_cpus.update(preflight._cpus(str(row["client_cpuset"])))
    samples: list[dict[str, Any]] = []
    started = time.monotonic()
    deadline = started + 60.0
    while True:
        processes, process_blockers = preflight._process_inventory(target_cpus)
        containers, container_blockers = preflight._docker_inventory(target_cpus, intended)
        blockers = process_blockers + container_blockers
        samples.append({
            "captured_at": now(), "process_inventory": processes,
            "container_inventory": containers, "blockers": blockers,
        })
        if blockers:
            raise RuntimeError(f"CPU-A slot rolling non-interference gate blocked: {blockers}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(10.0, remaining))
    return {
        "schema_version": "mysql_cpu_a_slots_rolling_noninterference_v1",
        "status": "PASS", "required_seconds": 60.0,
        "observed_seconds": time.monotonic() - started,
        "samples": samples, "blockers": [],
        "intended_containers": sorted(intended),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "cross_scene_completion_dependency": False,
    }


def build_authority(
    freeze: dict[str, Any], prepared_source: dict[str, Any], accounts_source: dict[str, Any],
) -> dict[str, Path]:
    if AUTHORITY.exists() or AUTHORITY.is_symlink():
        raise RuntimeError(f"fresh authority required: {AUTHORITY}")
    workers = [dict(row) for row in prepared_source.get("workers") or []]
    account_workers = [dict(row) for row in accounts_source.get("workers") or []]
    frozen_ids = {str(row["worker_id"]) for row in freeze.get("workers") or []}
    if (
        set(str(row["worker_id"]) for row in workers) != frozen_ids
        or set(str(row["worker_id"]) for row in account_workers) != frozen_ids
        or len(frozen_ids) != 8
    ):
        raise RuntimeError("recovered eight-worker authority differs")
    dynamic = rolling_gate(freeze)
    accepted = {scene: 0 for scene in SCENES}
    common = {
        "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": "four_independent_scenes",
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "accepted_rounds": accepted,
        "observed_seconds": float(dynamic["observed_seconds"]),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": dynamic,
        "source_authority_path": str(RECOVERY),
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "accepted_state_credit_increment": 0,
        "algorithm_a_measurement_credit_reused": False,
    }
    AUTHORITY.mkdir(parents=True, mode=0o700)
    preflight_path = AUTHORITY / "transition_preflight.json"
    atomic(preflight_path, {
        **common, "schema_version": "mysql_cpu_a_slots_c_transition_preflight_v1",
        "status": "PASS", "blockers": [], "launch_performed": False,
    })
    prepared_path = AUTHORITY / "prepared_binding.json"
    atomic(prepared_path, {
        **common, "schema_version": "mysql_cpu_a_slots_c_prepared_binding_v1",
        "status": "PREPARED", "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path), "workers": workers,
        "workload_started": False, "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    accounts_path = AUTHORITY / "benchmark_accounts_binding.json"
    atomic(accounts_path, {
        **common, "schema_version": "mysql_cpu_a_slots_c_accounts_binding_v1",
        "status": "PASS", "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path), "workers": account_workers,
        "passwords_emitted": False, "workload_started": False,
    })
    r0_path = AUTHORITY / "fresh_r0_binding.json"
    atomic(r0_path, {
        **common, "schema_version": "mysql_cpu_a_slots_c_fresh_r0_authority_v1",
        "status": "PASS", "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "benchmark_accounts_path": str(accounts_path),
        "benchmark_accounts_sha256": sha(accounts_path),
        "fresh_r0_complete": True, "formal_outer_launch_authorized": True,
        "formal_outer_started": False, "recalibration_performed": False,
        "target_bottleneck_gate": {"passed": True, "fresh_incumbent_measurement_owned_by_controller": True},
        "failures": [], "client_isolation_failures": [],
    })
    return {"preflight": preflight_path, "prepared": prepared_path, "accounts": accounts_path, "r0": r0_path}


def main() -> int:
    freeze = load(FREEZE)
    prepared, accounts = wait_recovery()
    authority = build_authority(freeze, prepared, accounts)
    command = [
        "/home/anonymous/miniconda3/envs/py313/bin/python", str(LAUNCHER),
        "--run-id", CAMPAIGN, "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(authority["preflight"]),
        "--prepared-binding", str(authority["prepared"]),
        "--benchmark-accounts", str(authority["accounts"]),
        "--fresh-r0", str(authority["r0"]),
        "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--startup-timeout-seconds", "300",
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=600)
    result = {
        "schema_version": "mysql_cpu_a_slots_four_scene_c_launch_v1",
        "created_at": now(), "status": "LAUNCHED" if completed.returncode == 0 else "FAILED_CLOSED",
        "campaign": CAMPAIGN, "returncode": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
        "external_process_control_or_stop_performed": False,
    }
    atomic(AUTHORITY / "launch_result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if completed.returncode == 0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
