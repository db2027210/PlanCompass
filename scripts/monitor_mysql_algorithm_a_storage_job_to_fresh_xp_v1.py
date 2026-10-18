#!/usr/bin/env python3
"""After JOB A completes, start fresh serialized XP runs and expand them to four."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_job_to_xp_slots_v1 as slots

HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v4"
    / "transition_contract.json"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v6"
    / "job_four_worker_resource_successor.json"
)
SUCCESSOR_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v4"
    / "xp_four_worker_resource_successor.json"
)
TRIGGER_PATH = LOG_ROOT / "v54_to_a_storage_compatible_20260823.job_to_fresh_xp_v4.trigger.json"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_storage_serialized_xp_fresh_v1.py"
EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_job_to_xp_scene_storage_v1.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
MANAGEMENT_CPUSET = "240-241,496-497"
RUNTIME_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
)
JOB_STATES = (
    HISTORY / "v55_to_a_storage_compatible_job4_20260823_attempt001_job" / "algorithm_a_state.json",
    HISTORY / "v54_to_a_storage_compatible_20260823_attempt001_job" / "algorithm_a_state.json",
)
XP = {
    "hybench_xp_cpu": {
        "run_id": "v56_to_a_storage_serialized_fresh_20260823_attempt001_xp_cpu",
        "prepared": HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001" / "prepared_binding.json",
        "accounts": HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001" / "benchmark_accounts_binding.json",
        "r0": HISTORY / "v36_to_c_per_scene_20260819_attempt037_xp_cpu_r0_recovered" / "r0_binding.json",
        "result": LOG_ROOT / "v54_to_a_storage_compatible_20260823.job_to_fresh_xp_v4.cpu_handoff.json",
        "executor_unit": "mysql-alga-storage-job-to-xp-transition-cpu-v4.service",
    },
    "hybench_xp_io": {
        "run_id": "v56_to_a_storage_serialized_fresh_20260823_attempt001_xp_io",
        "prepared": HISTORY / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery" / "prepared_binding.json",
        "accounts": HISTORY / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery" / "benchmark_accounts_binding.json",
        "r0": HISTORY / "v36_to_c_per_scene_20260819_attempt044_xp_io_r0_recovered" / "r0_binding.json",
        "result": LOG_ROOT / "v54_to_a_storage_compatible_20260823.job_to_fresh_xp_v4.io_handoff.json",
        "executor_unit": "mysql-alga-storage-job-to-xp-transition-io-v4.service",
    },
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT_PATH)
    if (
        contract.get("schema_version") != "mysql_algorithm_a_job_to_xp_slot_contract_v1"
        or contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("contract") != slots.TRANSITION_CONTRACT
        or contract.get("trigger_requires_job_complete_10") is not True
        or contract.get("xp_parallelism") != {"before": 2, "after": 4}
        or contract.get("fresh_xp_runs_required") is not True
        or contract.get("historical_accepted_rounds_reused") is not False
        or contract.get("shared_nvme_formal_waves_mutually_exclusive") is not True
    ):
        raise RuntimeError("storage-compatible JOB-to-XP contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"storage-compatible JOB-to-XP source differs: {path}")
    source = contract.get("source_resource_freeze") or {}
    if source.get("path") != str(SOURCE_FREEZE.relative_to(ROOT)) or sha(SOURCE_FREEZE) != source.get("sha256"):
        raise RuntimeError("storage-compatible JOB four-worker freeze differs")
    return contract


def current_job() -> tuple[Path, dict[str, Any]]:
    for path in JOB_STATES:
        if path.is_file() and not path.is_symlink():
            state = load(path)
            if state.get("status") in {"RUNNING", "COMPLETE"}:
                return path, state
    raise RuntimeError("current JOB Algorithm A state is absent")


def job_complete(state: Mapping[str, Any]) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == "job_memory"
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
        and state.get("round_checkpoint") is None
    )


def fresh_binding(scene: str) -> dict[str, Any]:
    row = XP[scene]
    run_id = str(row["run_id"])
    output = HISTORY / run_id
    suffix = "xp_cpu" if scene == "hybench_xp_cpu" else "xp_io"
    return {
        "scene": scene,
        "state": str(output / "algorithm_a_state.json"),
        "unit": f"mysql-alga-storage-xp-{run_id}.service",
        "output": str(output),
        "result": str(row["result"]),
        "suffix": suffix,
    }


def ensure_fresh_launch(scene: str, job_state_path: Path) -> dict[str, Any]:
    binding = fresh_binding(scene)
    state_path = Path(binding["state"])
    if state_path.is_file():
        state = load(state_path)
        if (
            state.get("async_scene") != scene
            or state.get("algorithm") != "A"
            or int(state.get("accepted") or 0) >= 10
        ):
            raise RuntimeError(f"existing fresh XP state differs: {state_path}")
        return binding
    row = XP[scene]
    command = [
        str(PYTHON), str(LAUNCHER), "--run-id", str(row["run_id"]),
        "--scene", scene, "--job-state", str(job_state_path),
        "--runtime-preflight", str(RUNTIME_PREFLIGHT),
        "--prepared-binding", str(row["prepared"]),
        "--benchmark-accounts", str(row["accounts"]),
        "--fresh-r0", str(row["r0"]),
        "--output-dir", binding["output"],
    ]
    completed = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, timeout=360,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"fresh {scene} launch failed: {completed.stderr[-2000:]}")
    return binding


def seal_trigger(job_state_path: Path, job: Mapping[str, Any]) -> dict[str, Any]:
    contract = validate_contract()
    job_evidence = {
        "complete": True, "accepted": 10,
        "state_path": str(job_state_path), "state_sha256": sha(job_state_path),
        "run_id": job.get("run_id"),
        "physical_replay_parallelism": int(job.get("physical_replay_parallelism") or 0),
    }
    source = load(SOURCE_FREEZE)
    additions = slots.build_xp_workers(source)
    successor = slots.build_successor_freeze(
        source, additions, created_at=now(), job_evidence={"job_memory": job_evidence},
    )
    if SUCCESSOR_FREEZE.exists() or SUCCESSOR_FREEZE.is_symlink():
        existing = load(SUCCESSOR_FREEZE)
        if (
            existing.get("schema_version")
            != "mysql_algorithm_a_xp_four_worker_after_job_resource_successor_v1"
            or existing.get("formal_scope") != "mysql_only"
            or existing.get("cross_engine_comparison_prohibited") is not True
            or {row.get("worker_id") for row in existing.get("workers") or []}
            != {row.get("worker_id") for row in successor.get("workers") or []}
        ):
            raise RuntimeError("existing XP successor freeze differs")
        successor = existing
    else:
        atomic(SUCCESSOR_FREEZE, successor)
    trigger = {
        "schema_version": "mysql_algorithm_a_job_to_xp_trigger_v1",
        "status": "READY", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "contract": slots.TRANSITION_CONTRACT,
        "contract_path": str(CONTRACT_PATH), "contract_sha256": sha(CONTRACT_PATH),
        "source_freeze_path": str(SOURCE_FREEZE),
        "source_freeze_sha256": sha(SOURCE_FREEZE),
        "successor_freeze_path": str(SUCCESSOR_FREEZE),
        "successor_freeze_sha256": sha(SUCCESSOR_FREEZE),
        "job_terminal_evidence": job_evidence,
        "xp_scenes": {scene: fresh_binding(scene) for scene in slots.XP_SCENES},
        "per_scene_independent_transition": True,
        "native_xp_workload_unchanged": True,
        "fresh_xp_runs_required": True,
        "historical_accepted_rounds_reused": False,
        "shared_nvme_formal_waves_mutually_exclusive": True,
        "contract_release": contract.get("release_version"),
    }
    atomic(TRIGGER_PATH, trigger)
    return trigger


def ready_for_transition(binding: Mapping[str, Any]) -> bool:
    path = Path(str(binding["state"]))
    if not path.is_file():
        return False
    state = load(path)
    return bool(
        state.get("status") == "RUNNING"
        and state.get("formal_started") is True
        and int(state.get("round_index") or 0) >= 1
        and state.get("round_checkpoint") is not None
        and int(state.get("accepted") or 0) < 10
    )


def launch_executor(scene: str) -> dict[str, Any]:
    row = XP[scene]
    suffix = "cpu" if scene == "hybench_xp_cpu" else "io"
    unit = str(row["executor_unit"])
    result = Path(str(row["result"]))
    stdout = LOG_ROOT / f"v54_to_a_storage_compatible_20260823.job_to_fresh_xp_v4.{suffix}.stdout.log"
    stderr = LOG_ROOT / f"v54_to_a_storage_compatible_20260823.job_to_fresh_xp_v4.{suffix}.stderr.log"
    if result.is_file():
        return {"scene": scene, "unit": unit, "result": str(result), "existing": True}
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A storage-compatible {scene} four-worker transition",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(EXECUTOR), "--scene", scene,
        "--trigger", str(TRIGGER_PATH), "--result", str(result),
        "--timeout-seconds", "172800",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError(f"{scene} transition executor launch failed: {completed.stderr}")
    return {"scene": scene, "unit": unit, "result": str(result),
            "stdout": str(stdout), "stderr": str(stderr), "existing": False}


def run(args: argparse.Namespace) -> int:
    validate_contract()
    output = args.output.resolve()
    launches: dict[str, Any] = {}
    executors: dict[str, Any] = {}
    while True:
        job_path, job = current_job()
        done = job_complete(job)
        bindings: dict[str, Any] = {}
        if done:
            for scene in slots.XP_SCENES:
                bindings[scene] = ensure_fresh_launch(scene, job_path)
            if not TRIGGER_PATH.is_file():
                seal_trigger(job_path, job)
            for scene, binding in bindings.items():
                if ready_for_transition(binding) and scene not in executors:
                    executors[scene] = launch_executor(scene)
        scene_rows = {}
        all_complete = done
        for scene in slots.XP_SCENES:
            binding = bindings.get(scene) or fresh_binding(scene)
            state_path = Path(binding["state"])
            state = load(state_path) if state_path.is_file() else {}
            result_path = Path(str(XP[scene]["result"]))
            handoff = load(result_path) if result_path.is_file() else None
            if handoff and handoff.get("status") == "LAUNCHED":
                launched = handoff.get("launch") or {}
                successor_state = Path(str(launched.get("state") or ""))
                if successor_state.is_file():
                    state = load(successor_state)
                    binding = {**binding, **{
                        "state": str(successor_state), "unit": launched.get("unit"),
                        "output": launched.get("output"),
                    }}
            terminal = bool(
                state.get("status") == "COMPLETE"
                and state.get("converged") is True
                and int(state.get("accepted") or 0) == 10
            )
            all_complete = all_complete and terminal
            scene_rows[scene] = {
                "state_path": binding["state"], "status": state.get("status"),
                "stage": state.get("stage"), "accepted": int(state.get("accepted") or 0),
                "round_index": int(state.get("round_index") or 0),
                "physical_replay_parallelism": int(state.get("physical_replay_parallelism") or 0),
                "terminal": terminal, "handoff": handoff,
            }
        status = (
            "COMPLETE" if all_complete else
            "XP_RUNNING" if done else "MONITORING_JOB"
        )
        document = {
            "schema_version": "mysql_algorithm_a_storage_job_to_fresh_xp_monitor_v1",
            "status": status, "updated_at": now(), "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "job": {"state_path": str(job_path), "status": job.get("status"),
                    "stage": job.get("stage"), "accepted": int(job.get("accepted") or 0),
                    "complete": done},
            "xp_scenes": scene_rows, "fresh_launches": launches,
            "executors": executors, "trigger_path": str(TRIGGER_PATH),
            "policy": {"fresh_xp_runs_required": True,
                       "historical_accepted_rounds_reused": False,
                       "shared_nvme_formal_waves_mutually_exclusive": True,
                       "xp_parallelism_before": 2, "xp_parallelism_after": 4},
        }
        atomic(output, document)
        if status == "COMPLETE":
            return 0
        time.sleep(args.poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    args = parser.parse_args()
    if not 5 <= args.poll_seconds <= 300:
        parser.error("poll seconds must be in [5, 300]")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
