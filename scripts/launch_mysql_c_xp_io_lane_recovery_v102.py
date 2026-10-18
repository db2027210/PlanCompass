#!/usr/bin/env python3
"""Launch the one-lane baseline recovery for the remaining XP-I/O C run."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
CAMPAIGN_ID = "v1210_mysql_c_xp_io_lane_recovery_v102_20260904"
RUN_ID = "v1210_to_c_xp_io_lane_recovery_v102_20260904_xp_io"
CAMPAIGN = HISTORY / CAMPAIGN_ID
RUN_ROOT = HISTORY / RUN_ID
MANIFEST = CAMPAIGN / "launch_manifest.json"
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_c_xp_io_lane_recovery_20260904_v102/"
    "recovery_contract.json"
)
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v12.py"
AUTHORITY = HISTORY / (
    "v1208_to_c_xp_io_lane_recovery_authority_v102_20260904/authority"
)
FRESH_R0 = HISTORY / (
    "v1209_to_c_xp_io_lane_recovery_fresh_r0_v102_20260904/fresh_r0_binding.json"
)
CHECKPOINT = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json"
)
QUARANTINE = HISTORY / (
    "v1185_to_c_client_primary_bootstrap_20260903/"
    "replay_quarantine/replay_quarantine.json"
)
BARRIER = Path("/tmp/mysql_async_outer_start") / CAMPAIGN_ID / "algorithm_c"


def now() -> str:
    return datetime.now().astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
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


def validate() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "hybench_xp_io"
        or contract.get("algorithm") != "C"
        or contract.get("fresh_search") is not True
        or contract.get("recovery_scope") != "formal_incumbent_baseline_only"
        or contract.get("retained_worker") != "xp_io_w1"
        or contract.get("retried_workers") != ["xp_io_w2"]
        or contract.get("stability_threshold_changed") is not False
        or contract.get("native_xp_workload_changed") is not False
        or contract.get("resource_envelope_changed") is not False
        or contract.get("completed_measurement_replay_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("outer_acceptance")
        != "strictly_higher_qps_than_previous_incumbent"
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("whole_round_resource_lease") is not False
        or contract.get("lease_mode") != "none"
    ):
        raise RuntimeError("XP-I/O C baseline lane-recovery contract differs")
    for name, raw in dict(contract.get("source_bindings") or {}).items():
        binding = dict(raw or {})
        path = (ROOT / str(binding.get("path") or "")).resolve()
        if not path.is_file() or path.is_symlink() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"XP-I/O C recovery source differs: {name}: {path}")
    return contract


def snapshot() -> dict[str, Any]:
    path = RUN_ROOT / "run_state.json"
    if not path.is_file():
        return {"status": "STARTING", "state_path": str(path)}
    state = load(path)
    return {
        "status": state.get("status"), "stage": state.get("stage"),
        "error": state.get("error"), "outer_attempt": state.get("outer_attempt"),
        "accepted_rounds": state.get("accepted_rounds"),
        "rejected_outer_attempts": state.get("rejected_outer_attempts"),
        "converged": state.get("converged"), "state_path": str(path),
        "state_sha256": sha(path),
    }


def main() -> int:
    validate()
    required = [
        CONTROLLER, CHECKPOINT, QUARANTINE, FRESH_R0,
        AUTHORITY / "transition_preflight.json",
        AUTHORITY / "prepared_binding.json",
        AUTHORITY / "benchmark_accounts_binding.json",
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular XP-I/O recovery artifact required: {path}")
    if any(path.exists() or path.is_symlink() for path in (CAMPAIGN, RUN_ROOT, BARRIER)):
        raise RuntimeError("fresh XP-I/O C recovery roots required")
    CAMPAIGN.mkdir(parents=True, mode=0o700)
    BARRIER.mkdir(parents=True, mode=0o700)
    log_path = CAMPAIGN / "xp_io.controller.log"
    command = [
        sys.executable, str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(CHECKPOINT),
        "--runtime-preflight", str(AUTHORITY / "transition_preflight.json"),
        "--prepared-binding", str(AUTHORITY / "prepared_binding.json"),
        "--benchmark-accounts", str(AUTHORITY / "benchmark_accounts_binding.json"),
        "--fresh-r0", str(FRESH_R0),
        "--replay-quarantine", str(QUARANTINE),
        "--fresh-search", "--scene", "hybench_xp_io",
        "--scene-start-barrier", str(BARRIER),
    ]
    environment = os.environ.copy()
    environment["MYSQL_TWO_WORKER_AUTHORITY_ROOT"] = str(AUTHORITY)
    manifest: dict[str, Any] = {
        "schema_version": "mysql_c_xp_io_single_failed_baseline_lane_recovery_launch_v102",
        "status": "STARTING", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "active_scene": "hybench_xp_io", "fresh_search": True,
        "retained_worker": "xp_io_w1", "retried_workers": ["xp_io_w2"],
        "retained_measurement_replayed": False,
        "stability_threshold_changed": False,
        "native_xp_workload_changed": False,
        "resource_envelope_changed": False,
        "post_reset_settle_decision_scope": "selected_xp_io_worker_cgroups",
        "four_scene_wave_barrier": False, "sibling_scene_wait": False,
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False, "lease_mode": "none",
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
        "contract_path": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "launcher_source": str(Path(__file__).resolve()),
        "launcher_source_sha256": sha(Path(__file__).resolve()),
        "run_id": RUN_ID, "log_path": str(log_path), "command": command,
    }
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=ROOT, env=environment, stdout=log,
            stderr=subprocess.STDOUT, text=True, start_new_session=True,
        )
        manifest.update({
            "status": "RUNNING", "pid": process.pid, "process_status": "RUNNING",
            "started_at": now(), "state": snapshot(), "updated_at": now(),
        })
        atomic(MANIFEST, manifest)
        while process.poll() is None:
            manifest["state"] = snapshot()
            manifest["updated_at"] = now()
            atomic(MANIFEST, manifest)
            time.sleep(10)
        code = int(process.returncode or 0)
    manifest.update({
        "returncode": code, "process_status": "EXITED", "state": snapshot(),
        "status": "COMPLETE" if code == 0 else "FAILED_CLOSED", "updated_at": now(),
    })
    atomic(MANIFEST, manifest)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
