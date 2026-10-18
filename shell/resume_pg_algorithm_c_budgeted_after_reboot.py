#!/usr/bin/env python3
"""Resume the existing budgeted C pilot only after a verified host reboot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_budget_policy import ACTIVE_TIME_CONTRACT, CONTRACT  # noqa: E402
from shell.launch_pg_algorithm_c_budgeted_trial import (  # noqa: E402
    command, cpus, inspect_peer_snapshot, launch,
)

SCENES = ("job_memory", "tpch_memory", "xp_cpu", "xp_io")
ALLOWED_RECOVERY_SOURCE_TRANSITIONS = frozenset({
    "algorithm_c_budget_policy.py",
    "shell/run_pg_algorithm_c_budgeted_scene.py",
    "shell/test_algorithm_c_budget_policy.py",
})


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def boot_epoch_s() -> float:
    for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise RuntimeError("host boot time is unavailable")


def process_exists(pid: int) -> bool:
    return pid > 0 and Path(f"/proc/{pid}").exists()


def recovery_preflight(root: Path) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    manifest = json.loads((root / "budgeted_launch_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("contract") != CONTRACT:
        raise RuntimeError("unexpected campaign contract")
    prior_status = json.loads((root / "launcher_status.json").read_text(encoding="utf-8"))
    live_old = [pid for pid in [*prior_status.get("pids", {}).values(), prior_status.get("support_pid")]
                if isinstance(pid, int) and process_exists(pid)]
    errors: List[str] = []
    if live_old:
        errors.append(f"prior_trial_processes_still_live:{live_old}")

    source_transitions = []
    for relative, expected in manifest["source_sha256"].items():
        path = ROOT / relative
        actual = sha256(path) if path.is_file() else "missing"
        if actual != expected:
            source_transitions.append({"path": relative, "before_sha256": expected, "after_sha256": actual})
            if relative not in ALLOWED_RECOVERY_SOURCE_TRANSITIONS:
                errors.append(f"unauthorized_source_change:{relative}")
    if {row["path"] for row in source_transitions} != set(ALLOWED_RECOVERY_SOURCE_TRANSITIONS):
        errors.append("expected_active_time_recovery_source_transition_missing")

    configs: Dict[str, Any] = {}
    workers = []
    state_rows = []
    trial_contract_migrations = []
    latest_activity = 0.0
    for scene in SCENES:
        entry = manifest["scenes"][scene]
        cfg = json.loads(Path(entry["config"]).read_text(encoding="utf-8"))
        configs[scene] = cfg
        workers.extend(cfg["binary_plan_tuning"]["validation"]["docker_workers"]["workers"])
        state_path = root / scene / "algorithm_c_state.json"
        log_path = root / scene / "controller.log"
        if not state_path.is_file():
            errors.append(f"missing_trial_state:{scene}")
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        trial = state.get("budgeted_trial") or {}
        if trial.get("contract") == ACTIVE_TIME_CONTRACT:
            trial_contract_migrations.append(scene)
        elif trial.get("contract") != CONTRACT:
            errors.append(f"invalid_trial_state_contract:{scene}")
        if state.get("phase") in {"budgeted_trial_finished_clean", "budgeted_trial_finalizing"}:
            errors.append(f"non_resumable_phase:{scene}:{state.get('phase')}")
        activity = max(state_path.stat().st_mtime, log_path.stat().st_mtime if log_path.is_file() else 0.0)
        latest_activity = max(latest_activity, activity)
        state_rows.append({
            "scene": scene,
            "phase": state.get("phase"),
            "round_index": state.get("round_index"),
            "last_durable_activity_epoch_s": activity,
            "inflight_labels": [row.get("label") for row in
                                (state.get("inflight_physical_wave") or {}).get("entries", [])],
            "search_replays_before_recovery": trial.get("search_replays"),
        })

    booted = boot_epoch_s()
    if booted <= latest_activity:
        errors.append("current_host_boot_does_not_postdate_trial_activity")
    names = [worker["container"] for worker in workers]
    if len(names) != 8 or len(set(names)) != 8:
        errors.append("four_scenes_require_eight_distinct_workers")
    inspected = json.loads(command(["docker", "inspect", *names]))
    by_name = {row["Name"].lstrip("/"): row for row in inspected}
    topology = {}
    for line in command(["lscpu", "-p=CPU,CORE,SOCKET"]).splitlines():
        if line and not line.startswith("#"):
            cpu, core, socket = line.split(",")
            topology[int(cpu)] = (int(socket), int(core))
    our_cpus = set()
    for worker in workers:
        actual = by_name[worker["container"]]
        expected_cpus = cpus(worker["cpuset_cpus"])
        our_cpus.update(expected_cpus)
        if cpus(actual["HostConfig"]["CpusetCpus"]) != expected_cpus:
            errors.append(f"worker_cpuset_mismatch:{worker['container']}")
        mounts = {row["Destination"]: row["Source"] for row in actual["Mounts"]}
        if mounts.get(worker["container_data_dir"]) != worker["data_dir"] or not worker["data_dir"].startswith("/disk1/"):
            errors.append(f"worker_disk1_mount_mismatch:{worker['container']}")
    for entry in manifest["scenes"].values():
        our_cpus.update(cpus(entry["controller_cpus"]))
    our_cores = {topology[cpu] for cpu in our_cpus}
    peer_ids = command(["docker", "ps", "-q"]).split()
    peers, disappeared = inspect_peer_snapshot(peer_ids)
    overlaps = []
    for peer in peers:
        name = peer["Name"].lstrip("/")
        bound = cpus(peer["HostConfig"]["CpusetCpus"])
        shared = sorted(our_cores & {topology[cpu] for cpu in bound if cpu in topology})
        if shared and name not in names:
            overlaps.append({"container": name, "shared_physical_cores": shared})
    if overlaps:
        samples = command(["docker", "stats", "--no-stream", "--format", "{{json .}}",
                           *[row["container"] for row in overlaps]], timeout=90)
        statistics = {row["Name"]: row for row in map(json.loads, samples.splitlines())}
        for row in overlaps:
            usage = float(str(statistics.get(row["container"], {}).get("CPUPerc", "100%")).rstrip("%"))
            row["cpu_percent_snapshot"] = usage
            if usage >= 2.0:
                errors.append(f"active_physical_core_overlap:{row['container']}:{usage}%")

    receipt = {
        "contract": "algorithm_c_budgeted_host_reboot_recovery_v1",
        "passed": not errors,
        "errors": errors,
        "checked_at_epoch_s": time.time(),
        "current_host_boot_epoch_s": booted,
        "latest_prior_activity_epoch_s": latest_activity,
        "host_reboot_verified": booted > latest_activity,
        "active_time_contract": ACTIVE_TIME_CONTRACT,
        "host_offline_time_excluded_from_budget": True,
        "inflight_missing_terminal_rows_become_technical_failures": True,
        "inflight_candidates_are_not_replayed": True,
        "source_transitions": source_transitions,
        "recovery_launcher_sha256": sha256(Path(__file__)),
        "prior_launcher_status": prior_status,
        "state_rows": state_rows,
        "trial_contract_migrations": trial_contract_migrations,
        "trial_contract_migration_preserves_search_state": True,
        "worker_names": names,
        "overlapping_peer_containers": overlaps,
        "peers_removed_during_snapshot": disappeared,
        "trial_only_not_publication_comparison": True,
    }
    (root / "host_reboot_recovery_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return manifest, configs, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_root", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.campaign_root.resolve()
    manifest, configs, receipt = recovery_preflight(root)
    print(json.dumps(receipt, indent=2), flush=True)
    if not receipt["passed"]:
        return 2
    if args.check_only:
        return 0
    return launch(root, manifest, configs, receipt)


if __name__ == "__main__":
    raise SystemExit(main())
