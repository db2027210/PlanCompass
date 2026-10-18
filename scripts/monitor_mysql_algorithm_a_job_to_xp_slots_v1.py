#!/usr/bin/env python3
"""Monitor four-worker JOB and independently release its slots to XP."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
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
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260821_v1"
    / "transition_contract.json"
)
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_dynamic_slots_20260821_v1"
    / "job_four_worker_resource_successor.json"
)
SUCCESSOR_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260821_v1"
    / "xp_four_worker_resource_successor.json"
)
TRIGGER_PATH = LOG_ROOT / "v39_to_a_policy10_20260821.job_to_xp.trigger.json"
EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_job_to_xp_scene_v1.py"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
MANAGEMENT_CPUSET = "240-241,496-497"
JOB = {
    "scene": "job_memory",
    "state": str(HISTORY / "v39_to_a_policy10_job_dynamic_slots_20260821_attempt001_job" / "algorithm_a_state.json"),
    "unit": "mysql-alga-dynamic-v39_to_a_policy10_job_dynamic_slots_20260821_attempt001_job.service",
    "output": str(HISTORY / "v39_to_a_policy10_job_dynamic_slots_20260821_attempt001_job"),
}
XP = {
    "hybench_xp_cpu": {
        "scene": "hybench_xp_cpu",
        "state": str(HISTORY / "v39_to_a_policy10_20260821_attempt009_xp_cpu" / "algorithm_a_state.json"),
        "unit": "mysql-alga-policy10-attempt009-xp-cpu.service",
        "output": str(HISTORY / "v39_to_a_policy10_20260821_attempt009_xp_cpu"),
        "result": str(LOG_ROOT / "v39_to_a_policy10_20260821.job_to_xp.cpu_handoff.json"),
    },
    "hybench_xp_io": {
        "scene": "hybench_xp_io",
        "state": str(HISTORY / "v39_to_a_policy10_20260821_attempt009_xp_io" / "algorithm_a_state.json"),
        "unit": "mysql-alga-policy10-attempt009-xp-io.service",
        "output": str(HISTORY / "v39_to_a_policy10_20260821_attempt009_xp_io"),
        "result": str(LOG_ROOT / "v39_to_a_policy10_20260821.job_to_xp.io_handoff.json"),
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
        or contract.get("contract") != slots.TRANSITION_CONTRACT
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("JOB-to-XP contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding.get("path") or "")
        if path.is_symlink() or not path.is_file() or sha(path) != binding.get("sha256"):
            raise RuntimeError(f"JOB-to-XP source binding differs: {path}")
    source = contract.get("source_resource_freeze") or {}
    if sha(SOURCE_FREEZE) != source.get("sha256"):
        raise RuntimeError("four-worker JOB source freeze differs")
    return contract


def job_complete(state: Mapping[str, Any]) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == "job_memory"
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
        and float(state.get("normalized_width") or 1.0) <= 0.01
        and state.get("round_checkpoint") is None
        and (state.get("completion_contract") or {}).get(
            "postgresql_algorithm_a_flow_matched"
        ) is True
    )


def xp_terminal(state: Mapping[str, Any], scene: str) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("status") in {"COMPLETE", "NONCONVERGENCE_CLEAN"}
        and state.get("round_checkpoint") is None
    )


def current_xp_binding(scene: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    binding = dict(XP[scene])
    result_path = Path(str(binding["result"]))
    result = load(result_path) if result_path.is_file() else None
    if result and result.get("status") == "LAUNCHED":
        launch = result.get("launch") or {}
        binding.update({
            "state": launch["state"], "unit": launch["unit"],
            "output": launch["output"],
        })
    return binding, result


def seal_trigger(job_state: Mapping[str, Any]) -> dict[str, Any]:
    contract = validate_contract()
    job_evidence = {
        "complete": True, "accepted": 10,
        "state_path": JOB["state"], "state_sha256": sha(Path(JOB["state"])),
        "run_id": job_state.get("run_id"),
        "physical_replay_parallelism": int(job_state.get("physical_replay_parallelism") or 0),
    }
    source = load(SOURCE_FREEZE)
    additions = slots.build_xp_workers(source)
    successor = slots.build_successor_freeze(
        source, additions, created_at=now(),
        job_evidence={"job_memory": job_evidence},
    )
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
        "xp_scenes": copy_json(XP),
        "per_scene_independent_transition": True,
        "native_xp_workload_unchanged": True,
        "contract_release": contract.get("release_version"),
    }
    atomic(TRIGGER_PATH, trigger)
    return trigger


def copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def launch_executor(scene: str) -> dict[str, Any]:
    suffix = "cpu" if scene == "hybench_xp_cpu" else "io"
    unit = f"mysql-alga-job-to-xp-transition-{suffix}.service"
    result = Path(str(XP[scene]["result"]))
    stdout = LOG_ROOT / f"v39_to_a_policy10_20260821.job_to_xp.{suffix}.stdout.log"
    stderr = LOG_ROOT / f"v39_to_a_policy10_20260821.job_to_xp.{suffix}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm A independent {scene} slot transition",
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
    return {
        "scene": scene, "unit": unit, "result": str(result),
        "stdout": str(stdout), "stderr": str(stderr),
    }


def run(args: argparse.Namespace) -> int:
    validate_contract()
    output = args.output.resolve()
    lock_path = output.with_name(output.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="ascii") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        executors_launched = TRIGGER_PATH.is_file()
        launches: dict[str, Any] = {}
        while True:
            job_state = load(Path(JOB["state"]))
            job_done = job_complete(job_state)
            scene_rows: dict[str, Any] = {}
            all_xp_terminal = True
            for scene in slots.XP_SCENES:
                binding, result = current_xp_binding(scene)
                state = load(Path(str(binding["state"])))
                terminal = xp_terminal(state, scene)
                all_xp_terminal = all_xp_terminal and terminal
                scene_rows[scene] = {
                    "state_path": binding["state"], "state_sha256": sha(Path(str(binding["state"]))),
                    "status": state.get("status"), "stage": state.get("stage"),
                    "accepted": int(state.get("accepted") or 0),
                    "round_index": int(state.get("round_index") or 0),
                    "physical_replay_parallelism": int(state.get("physical_replay_parallelism") or 0),
                    "terminal": terminal, "handoff": result,
                }
            if job_done and not executors_launched:
                seal_trigger(job_state)
                for scene in slots.XP_SCENES:
                    launches[scene] = launch_executor(scene)
                executors_launched = True
            status = (
                "COMPLETE" if job_done and all_xp_terminal else
                "HANDOFF_EXECUTORS_LAUNCHED" if executors_launched else
                "MONITORING_JOB"
            )
            document = {
                "schema_version": "mysql_algorithm_a_job_to_xp_monitor_v1",
                "status": status, "updated_at": now(), "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "contract": slots.TRANSITION_CONTRACT,
                "job": {
                    "state_path": JOB["state"], "state_sha256": sha(Path(JOB["state"])),
                    "status": job_state.get("status"), "stage": job_state.get("stage"),
                    "accepted": int(job_state.get("accepted") or 0),
                    "round_index": int(job_state.get("round_index") or 0),
                    "physical_replay_parallelism": int(job_state.get("physical_replay_parallelism") or 0),
                    "complete": job_done,
                },
                "xp_scenes": scene_rows,
                "trigger_path": str(TRIGGER_PATH),
                "trigger_exists": TRIGGER_PATH.is_file(),
                "executors_launched": executors_launched,
                "launches": launches,
                "policy": {
                    "job_complete_10_required": True,
                    "per_scene_independent_transition": True,
                    "round_boundary_required": True,
                    "xp_parallelism_after_transition": 4,
                },
            }
            atomic(output, document)
            print(json.dumps({
                "time": document["updated_at"], "status": status,
                "job_accepted": document["job"]["accepted"],
                "job_stage": document["job"]["stage"],
                "xp": {scene: {"accepted": row["accepted"], "stage": row["stage"]} for scene, row in scene_rows.items()},
            }, ensure_ascii=False, sort_keys=True), flush=True)
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
