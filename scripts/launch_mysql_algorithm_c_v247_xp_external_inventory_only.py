#!/usr/bin/env python3
"""Resume XP/CPU and XP/I/O concurrently under inventory-only external gating."""

from __future__ import annotations

from datetime import datetime
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v20 as checkpoint_v20
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v138 as controller


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v138.py"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_xp_external_inventory_only_20260929_v247/"
    "xp_external_inventory_only_contract.json"
)
FORMAL_R0_SET = HISTORY / "c_v152_formal_r0_set_v36_20260923/formal_r0_set.json"
SCENES = {
    "xp_cpu": {
        "scene": "hybench_xp_cpu", "affinity": "16-23,272-279",
        "completed": 22, "accepted": 13, "rejected": 9, "soft": 2,
        "checkpoint": HISTORY / "c_v247_xp_cpu_v237_outer22_external_gate_checkpoint_20260929/prepared_binding.json",
        "quarantine": HISTORY / "c_v247_xp_cpu_v237_outer22_external_gate_quarantine_v2_20260929/replay_quarantine.json",
        "authority": HISTORY / "c_v235_xp_cpu_live_identity_rebind_20260929/authority",
        "fresh_r0": HISTORY / "c_v235_xp_cpu_fresh_r0_identity_successor_20260929/fresh_r0_binding.json",
        "baseline": HISTORY / "c_v149_paper_baseline_calibration_20260923_xp_cpu/formal_incumbent_baseline/batch_result.json",
        "failed_state": HISTORY / "c_v237_xp_cpu_runtime_gate_recovery_20260929_xp_cpu/run_state.json",
    },
    "xp_io": {
        "scene": "hybench_xp_io", "affinity": "24-31,280-287",
        "completed": 18, "accepted": 7, "rejected": 11, "soft": 4,
        "checkpoint": HISTORY / "c_v247_xp_io_v211f_outer18_external_gate_checkpoint_20260929/prepared_binding.json",
        "quarantine": HISTORY / "c_v247_xp_io_v211f_outer18_external_gate_quarantine_v2_20260929/replay_quarantine.json",
        "authority": HISTORY / "c_v156_candidate_authority_v36_20260923/authority",
        "fresh_r0": HISTORY / "c_v156_candidate_fresh_r0_v36_20260923/fresh_r0_binding.json",
        "baseline": HISTORY / "c_v150_paper_baseline_calibration_20260923_xp_io/formal_incumbent_baseline/batch_result.json",
        "failed_state": HISTORY / "c_v211f_pg_dynamic_gate_four_scene_20260929_xp_io/run_state.json",
    },
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        left, separator, right = item.partition("-")
        result.update(range(int(left), int(right) + 1) if separator else [int(left)])
    return result


def validate() -> dict[str, Any]:
    contract = controller.validate_contract()
    for label, raw in SCENES.items():
        checkpoint = checkpoint_v20.verify_checkpoint(raw["checkpoint"])
        quarantine = load(raw["quarantine"])
        failed = load(raw["failed_state"])
        scene = raw["scene"]
        if (
            checkpoint.get("scene") != scene
            or int(checkpoint.get("completed_outer_attempts") or -1) != raw["completed"]
            or int(checkpoint["reconstructed_state"]["accepted_rounds"][scene]) != raw["accepted"]
            or int(checkpoint["reconstructed_state"]["rejected_outer_attempts"][scene]) != raw["rejected"]
            or quarantine.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
            or int(quarantine.get("resume_checkpoint_outer_attempt") or -1) != raw["completed"]
            or int((quarantine.get("soft_replayed_counts_by_scene") or {}).get(scene, -1)) != raw["soft"]
            or failed.get("status") != "FAILED_CLOSED"
            or failed.get("error") != "RuntimeError: dynamic non-interference gate failed closed"
            or int(failed.get("outer_attempt") or -1) != raw["completed"]
        ):
            raise RuntimeError(f"v247 {label} recovery authority differs")
    for raw in dict(contract.get("evidence_bindings") or {}).values():
        path = ROOT / str(raw.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != raw.get("sha256"):
            raise RuntimeError(f"v247 evidence differs: {path}")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-prefix", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,120}", args.run_prefix) is None:
        parser.error("unsafe run prefix")
    contract = validate()
    campaign = HISTORY / args.run_prefix
    barrier = Path("/tmp/mysql_async_outer_start") / args.run_prefix / "algorithm_c"
    outputs = {
        label: HISTORY / f"{args.run_prefix}_{label}" for label in SCENES
    }
    if campaign.exists() or barrier.exists() or any(path.exists() for path in outputs.values()):
        raise RuntimeError("fresh v247 XP recovery output required")
    campaign.mkdir(parents=True, mode=0o700)
    barrier.mkdir(parents=True, mode=0o700)
    processes: dict[str, subprocess.Popen[Any]] = {}
    launches: dict[str, Any] = {}
    for label, raw in SCENES.items():
        run_id = f"{args.run_prefix}_{label}"
        log_path = campaign / f"{label}.controller.log"
        log = log_path.open("w")
        environment = os.environ.copy()
        environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(raw["authority"])
        environment["MYSQL_C_RUNTIME_GATE_AUTHORITY_ROOT"] = str(
            campaign / f"{label}_runtime_gate_authority"
        )
        command = [
            sys.executable, str(CONTROLLER), "--run-id", run_id,
            "--checkpoint", str(raw["checkpoint"]),
            "--runtime-preflight", str(raw["authority"] / "transition_preflight.json"),
            "--prepared-binding", str(raw["authority"] / "prepared_binding.json"),
            "--benchmark-accounts", str(raw["authority"] / "benchmark_accounts_binding.json"),
            "--fresh-r0", str(raw["fresh_r0"]),
            "--replay-quarantine", str(raw["quarantine"]),
            "--scene", str(raw["scene"]),
            "--scene-start-barrier", str(barrier),
            "--formal-baseline", str(raw["baseline"]),
            "--formal-r0-set", str(FORMAL_R0_SET),
        ]
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=lambda affinity=str(raw["affinity"]): os.sched_setaffinity(0, cpus(affinity)),
        )
        log.close()
        processes[label] = process
        launches[label] = {
            "scene": raw["scene"], "run_id": run_id, "pid": process.pid,
            "controller_cpuset": raw["affinity"], "log": str(log_path),
            "output": str(outputs[label]),
            "checkpoint": str(raw["checkpoint"]),
            "checkpoint_sha256": sha(raw["checkpoint"]),
            "quarantine": str(raw["quarantine"]),
            "quarantine_sha256": sha(raw["quarantine"]),
            "completed_outer_attempts_preserved": raw["completed"],
            "accepted_decisions_preserved": raw["accepted"],
            "rejected_decisions_preserved": raw["rejected"],
            "soft_aborted_replays": raw["soft"],
        }
    manifest = {
        "schema_version": "mysql_algorithm_c_v247_xp_external_inventory_only_launch_v1",
        "release_version": contract["release_version"],
        "status": "RUNNING", "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": False,
        "strict_external_noninterference_evidence_eligible": False,
        "external_processes_inventory_only": True,
        "external_processes_can_block": False,
        "target_mysql_local_resource_proof_required": True,
        "native_xp_workload_unchanged": True,
        "scenes_started_concurrently": True,
        "controller": str(CONTROLLER), "controller_sha256": sha(CONTROLLER),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "launches": launches,
        "persistent_monitor_started": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(campaign / "launch_manifest.json", manifest)

    deadline = time.monotonic() + 300
    ready: set[str] = set()
    while time.monotonic() < deadline:
        for label, raw in SCENES.items():
            if label in ready:
                continue
            state_path = outputs[label] / "run_state.json"
            if state_path.is_file():
                state = load(state_path)
                if state.get("status") == "FAILED_CLOSED":
                    raise RuntimeError(f"v247 {label} failed: {state.get('error')}")
                if (
                    state.get("status") == "RUNNING"
                    and int(state.get("outer_attempt") or -1) >= raw["completed"]
                    and state.get("external_processes_inventory_only") is True
                    and state.get("formal_evidence_eligible") is False
                ):
                    ready.add(label)
            elif processes[label].poll() is not None:
                raise RuntimeError(f"v247 {label} exited before state publication")
        if ready == set(SCENES):
            print(json.dumps({
                "status": "RUNNING", "campaign": str(campaign),
                "pids": {label: process.pid for label, process in processes.items()},
                "ready_scenes": sorted(ready),
                "external_processes_inventory_only": True,
            }, sort_keys=True))
            return 0
        time.sleep(2)
    raise RuntimeError(
        "v247 XP controllers remain in initialization; background processes were not stopped"
    )


if __name__ == "__main__":
    raise SystemExit(main())
