#!/usr/bin/env python3
"""Launch independent Algorithm-C v73 empty-expansion-skip controllers."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python").resolve()
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v73.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v25"
    / "algorithm_c_async_contract.json"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"directory required: {path}")
    return path


def load(path: Path) -> dict[str, Any]:
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


def unit_fields(unit: str) -> dict[str, str]:
    shown = subprocess.run(
        [
            "systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            "-p", "Result", "-p", "ExecMainStatus",
        ],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=15,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def stop_units(units: list[str]) -> None:
    for unit in units:
        subprocess.run(
            ["systemctl", "--user", "stop", unit], cwd=ROOT,
            text=True, capture_output=True, check=False, timeout=30,
        )


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("release_version") != "v73"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("recalibration_required") is not False
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("post_search_stability_confirmation") is not False
        or (contract.get("partial_topk_settlement_recovery") or {}).get(
            "settle_when_all_selected_candidates_measured"
        ) is not True
    ):
        raise RuntimeError("Algorithm C v73 launch contract differs")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", type=regular_file, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--replay-quarantine", type=regular_file, required=True)
    parser.add_argument("--fresh-search", action="store_true")
    parser.add_argument("--resume-search", action="store_true")
    parser.add_argument("--formal-partial-root", type=directory)
    parser.add_argument("--formal-start-rank", type=int, default=1)
    parser.add_argument("--scene", choices=tuple(SCENES))
    parser.add_argument("--startup-timeout-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,110}", args.run_id) is None:
        parser.error("unsafe run id")
    if args.fresh_search == args.resume_search:
        parser.error("choose exactly one of fresh-search or resume-search")
    if args.formal_partial_root is None and args.formal_start_rank != 1:
        parser.error("formal partial root is required above rank 1")
    if args.formal_partial_root is not None and (
        not args.resume_search
        or args.scene is None
        or args.formal_start_rank not in range(2, 6)
    ):
        parser.error("partial continuation requires one resume scene and rank 2..5")
    if args.startup_timeout_seconds < 120:
        parser.error("startup timeout is too small for four authority readbacks")
    if not PYTHON.is_file() or PYTHON.is_symlink():
        raise RuntimeError("sealed Python interpreter absent")
    contract = validate_contract()
    target_scenes = (
        {args.scene: SCENES[args.scene]} if args.scene is not None else SCENES
    )
    run_ids = {
        scene: f"{args.run_id}_{suffix}" for scene, suffix in target_scenes.items()
    }
    outputs = {scene: HISTORY / run_id for scene, run_id in run_ids.items()}
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        raise RuntimeError("fresh per-scene Algorithm C outputs are required")
    barrier_root = Path("/tmp/mysql_async_outer_start") / args.run_id / "algorithm_c"
    if barrier_root.exists() or barrier_root.is_symlink():
        raise RuntimeError(f"fresh startup barrier required: {barrier_root}")
    barrier_root.mkdir(parents=True, mode=0o700)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)

    units: dict[str, str] = {}
    logs: dict[str, dict[str, str]] = {}
    started_units: list[str] = []
    try:
        for scene, run_id in run_ids.items():
            unit = f"mysql-algc-async-{run_id}.service"
            stdout = LOG_ROOT / f"{run_id}.stdout.log"
            stderr = LOG_ROOT / f"{run_id}.stderr.log"
            command = [
                "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
                f"--description=MySQL Algorithm C independent scene {scene}",
                f"--property=WorkingDirectory={ROOT}",
                "--property=KillMode=control-group",
                f"--property=StandardOutput=append:{stdout}",
                f"--property=StandardError=append:{stderr}",
                "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
                str(CONTROLLER), "--run-id", run_id,
                "--checkpoint", str(args.checkpoint),
                "--runtime-preflight", str(args.runtime_preflight),
                "--prepared-binding", str(args.prepared_binding),
                "--benchmark-accounts", str(args.benchmark_accounts),
                "--fresh-r0", str(args.fresh_r0),
                "--replay-quarantine", str(args.replay_quarantine),
                "--scene", scene,
                "--scene-start-barrier", str(barrier_root),
            ]
            if args.fresh_search:
                command.append("--fresh-search")
            if args.formal_partial_root is not None:
                command.extend([
                    "--formal-partial-root", str(args.formal_partial_root),
                    "--formal-start-rank", str(args.formal_start_rank),
                ])
            started = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True,
                check=False, timeout=30,
            )
            if started.returncode != 0:
                raise RuntimeError(
                    f"{scene} Algorithm C launch failed: {started.stderr[-1000:]}"
                )
            units[scene] = unit
            logs[scene] = {"stdout": str(stdout), "stderr": str(stderr)}
            started_units.append(unit)

        deadline = time.monotonic() + args.startup_timeout_seconds
        states: dict[str, dict[str, Any]] = {}
        fields: dict[str, dict[str, str]] = {}
        while time.monotonic() < deadline:
            ready = True
            for scene, unit in units.items():
                fields[scene] = unit_fields(unit)
                if fields[scene].get("ActiveState") in {"inactive", "failed"}:
                    raise RuntimeError(
                        f"{scene} Algorithm C exited during startup: {fields[scene]}; "
                        f"stderr={logs[scene]['stderr']}"
                    )
                state_path = outputs[scene] / "run_state.json"
                if not state_path.is_file():
                    ready = False
                    continue
                state = load(state_path)
                states[scene] = state
                if not (
                    state.get("status") == "RUNNING"
                    and state.get("algorithm") == "C"
                    and state.get("async_scene") == scene
                    and state.get("four_scene_wave_barrier") is False
                    and state.get("other_scene_completion_required") is False
                ):
                    ready = False
            if ready and len(states) == len(target_scenes):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("asynchronous Algorithm C startup timed out")
    except BaseException:
        stop_units(started_units)
        raise

    manifest = LOG_ROOT / f"{args.run_id}.async_c.launch_manifest.json"
    atomic(manifest, {
        "schema_version": "mysql_algorithm_c_async_scene_launch_manifest_v1",
        "status": "LAUNCHED",
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "campaign_id": args.run_id,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "fresh_search": bool(args.fresh_search),
        "resume_search": bool(args.resume_search),
        "formal_partial_root": (
            None if args.formal_partial_root is None
            else str(args.formal_partial_root)
        ),
        "formal_start_rank": args.formal_start_rank,
        "empty_candidate_ranks_require_no_gate_or_measurement": True,
        "four_scene_wave_barrier": False,
        "independent_handoff_scene": args.scene,
        "other_b_scene_completion_required": False,
        "per_scene_outer_state_machine": True,
        "scene_may_advance_immediately": True,
        "recalibration_performed": False,
        "external_observer_or_watchdog_started": False,
        "startup_barrier": str(barrier_root),
        "startup_barrier_is_per_round": False,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "controller_path": str(CONTROLLER),
        "controller_sha256": sha(CONTROLLER),
        "contract_path": str(CONTRACT),
        "contract_sha256": sha(CONTRACT),
        "contract_release": contract.get("release_version"),
        "authority": {
            "checkpoint": str(args.checkpoint),
            "runtime_preflight": str(args.runtime_preflight),
            "prepared_binding": str(args.prepared_binding),
            "benchmark_accounts": str(args.benchmark_accounts),
            "fresh_r0": str(args.fresh_r0),
            "replay_quarantine": str(args.replay_quarantine),
        },
        "scenes": {
            scene: {
                "run_id": run_ids[scene],
                "unit": units[scene],
                "main_pid": int(fields[scene].get("MainPID") or 0),
                "output": str(outputs[scene]),
                "run_state": str(outputs[scene] / "run_state.json"),
                "stage": states[scene].get("stage"),
                **logs[scene],
            }
            for scene in target_scenes
        },
    })
    print(json.dumps({
        "status": "LAUNCHED",
        "campaign_id": args.run_id,
        "manifest": str(manifest),
        "units": units,
        "stages": {scene: states[scene].get("stage") for scene in target_scenes},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
