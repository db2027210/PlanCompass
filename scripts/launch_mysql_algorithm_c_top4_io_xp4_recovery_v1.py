#!/usr/bin/env python3
"""Resume Top4 IO Algorithm C from outer 4 on its four-worker cohort."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
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

from scripts import build_mysql_algorithm_c_xp4_transition_checkpoint_v1 as rebind
from scripts import build_mysql_algorithm_c_empty_quarantine_for_checkpoint_v1 as quarantine


HISTORY = ROOT / "history/mysql_four_scene_20260809"
SCENE = "hybench_xp_io"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
    / "xp_four_worker_resource_successor.json"
)
BOUNDARY = (
    HISTORY / "v165_to_c_top4_io_outer4_boundary_20260826/prepared_binding.json"
)
LIVE = HISTORY / "v164_to_xp4_live_authority_20260826" / SCENE
OUTPUT = HISTORY / "v172_to_c_top4_io_xp4_recovery_20260826"
RUN_ID = "v172_to_c_top4_io_xp4_20260826_attempt006_xp_io"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v1.py"
UNIT = "mysql-algc-top4-io-xp4-v172.service"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
MANAGEMENT_CPUSET = "240-241,496-497"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


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


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        result = OUTPUT / "launch.json"
        if result.is_file():
            print(json.dumps(load(result), sort_keys=True))
            return 0
        raise RuntimeError("fresh IO C recovery output required")
    live = load(LIVE / "live_authority.json")
    boundary = rebind.verifier.verify_checkpoint(BOUNDARY)
    if (
        live.get("status") != "PASS"
        or live.get("scene") != SCENE
        or live.get("physical_replay_parallelism") != 4
        or boundary.get("completed_outer_attempts") != 4
        or (boundary.get("independent_scene_boundary") or {}).get("scene") != SCENE
    ):
        raise RuntimeError("IO C boundary or XP4 live authority differs")
    OUTPUT.mkdir(parents=True, mode=0o700)
    authority_path = OUTPUT / "transition_authority.json"
    source_freeze = Path(str(boundary["freeze_path"])).resolve()
    authority = {
        "schema_version": "mysql_algorithm_c_top4_io_xp4_transition_authority_v1",
        "status": "PASS", "created_at": now(), "scene": SCENE,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "round_boundary_preserved": True, "accepted_state_credit_increment": 0,
        "completed_candidate_replay_increment": 0, "recalibration_performed": False,
        "source_freeze_path": str(source_freeze),
        "source_freeze_sha256": sha(source_freeze),
        "successor_freeze_path": str(FREEZE),
        "successor_freeze_sha256": sha(FREEZE),
        "source_boundary_checkpoint_path": str(BOUNDARY),
        "source_boundary_checkpoint_sha256": sha(BOUNDARY),
        "worker_ids": list(live["worker_ids"]),
        "candidate_batches_before": [[1, 2], [3, 4], [5]],
        "candidate_batches_after": [[1, 2, 3, 4], [5]],
        "semantic_top_k": 5, "native_xp_workload_unchanged": True,
        "lease_mode": "none",
    }
    atomic(authority_path, authority)
    rebound_root = OUTPUT / "successor_checkpoint"
    rebound_root.mkdir(parents=True, mode=0o700)
    checkpoint = rebound_root / "prepared_binding.json"
    document = rebind.build(BOUNDARY, FREEZE, authority_path)
    rebind.base.alg.atomic(checkpoint, document)
    rebind.verifier.verify_checkpoint(checkpoint)
    quarantine_root = OUTPUT / "replay_quarantine"
    quarantine_root.mkdir(parents=True, mode=0o700)
    empty = {name: [] for name in quarantine.SCENES}
    zeros = {name: 0 for name in quarantine.SCENES}
    quarantine_document = {
        "schema_version": "mysql_algorithm_c_replay_quarantine_v6",
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze_sha256": document["freeze_sha256"],
        "resume_checkpoint_outer_attempt": document["completed_outer_attempts"],
        "formal_state_mutation": False, "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False, "interval_convergence_mutation": False,
        "quarantined_counts_by_scene": zeros,
        "soft_replayed_counts_by_scene": dict(zeros),
        "permanent_invalid_counts_by_scene": dict(zeros),
        "quarantined_param_keys_by_scene": empty,
        "soft_replayed_param_keys_by_scene": copy.deepcopy(empty),
        "permanent_invalid_param_keys_by_scene": copy.deepcopy(empty),
        "rows_by_scene": {name: [] for name in quarantine.SCENES},
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "initialization": "io_top4_outer4_xp4_boundary_recovery",
        "evidence": {
            "checkpoint": quarantine.bound(checkpoint),
            "resource_freeze": quarantine.bound(FREEZE),
            "pruning_contract": quarantine.bound(quarantine.PRUNING),
        },
    }
    quarantine_path = quarantine_root / "replay_quarantine.json"
    atomic(quarantine_path, quarantine_document)
    artifacts = live["artifacts"]
    run_root = HISTORY / RUN_ID
    unit = UNIT
    log_root = HISTORY / "formal_launch_logs"
    stdout = log_root / f"{RUN_ID}.stdout.log"
    stderr = log_root / f"{RUN_ID}.stderr.log"
    barrier = Path(f"/tmp/mysql_c_xp4_start/{RUN_ID}")
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL Top4 IO Algorithm C four-worker outer4 recovery",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
        str(PYTHON), str(CONTROLLER), "--run-id", RUN_ID,
        "--checkpoint", str(checkpoint),
        "--runtime-preflight", str(artifacts["preflight"]),
        "--prepared-binding", str(artifacts["prepared"]),
        "--benchmark-accounts", str(artifacts["accounts"]),
        "--fresh-r0", str(artifacts["r0"]),
        "--replay-quarantine", str(quarantine_path),
        "--scene", SCENE, "--scene-start-barrier", str(barrier),
    ]
    launched = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                              check=False, timeout=30)
    if launched.returncode != 0:
        raise RuntimeError(f"IO C recovery launch failed: {launched.stderr}")
    state_path = run_root / "run_state.json"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        shown = subprocess.run(
            ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID"],
            capture_output=True, text=True, check=False, timeout=15,
        ).stdout
        if "ActiveState=failed" in shown or "ActiveState=inactive" in shown:
            raise RuntimeError(f"IO C recovery exited during startup: {shown}; {stderr}")
        if state_path.is_file() and load(state_path).get("status") == "RUNNING":
            result = {
                "status": "LAUNCHED", "launched_at": now(), "scene": SCENE,
                "run_id": RUN_ID, "unit": unit, "state": str(state_path),
                "checkpoint": str(checkpoint), "output": str(run_root),
                "stdout": str(stdout), "stderr": str(stderr),
            }
            atomic(OUTPUT / "launch.json", result)
            print(json.dumps(result, sort_keys=True))
            return 0
        time.sleep(2)
    raise TimeoutError("IO C recovery startup evidence timed out")


if __name__ == "__main__":
    raise SystemExit(main())
