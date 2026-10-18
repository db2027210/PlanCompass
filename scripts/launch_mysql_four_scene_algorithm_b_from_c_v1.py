#!/usr/bin/env python3
"""Launch and verify formal four-scene Algorithm B without external mutation."""

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
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
WATCHDOG = ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v2.py"
MANAGEMENT_CPUSET = "240-241,496-497"
SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]


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


def regular(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {path}")
    return path


def unit_fields(unit: str, *names: str) -> dict[str, str]:
    command = ["systemctl", "--user", "show", unit]
    for name in names:
        command.extend(["-p", name])
    shown = subprocess.run(
        command, text=True, capture_output=True, check=True, timeout=10,
    ).stdout
    return dict(line.split("=", 1) for line in shown.splitlines() if "=" in line)


def stop(unit: str) -> None:
    subprocess.run(
        ["systemctl", "--user", "stop", unit], check=False, timeout=30,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--algorithm-b-release", type=regular, required=True)
    parser.add_argument("--checkpoint", type=regular, required=True)
    parser.add_argument("--runtime-preflight", type=regular, required=True)
    parser.add_argument("--prepared-binding", type=regular, required=True)
    parser.add_argument("--benchmark-accounts", type=regular, required=True)
    parser.add_argument("--fresh-r0", type=regular, required=True)
    parser.add_argument("--replay-quarantine", type=regular, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run-id")

    from scripts import build_mysql_algorithm_b_from_algorithm_c_release_v1 as release_mod

    release = release_mod.verify_release(args.algorithm_b_release)
    runtime = dict(release["algorithm_b_runtime_contract"])
    controller_binding = dict(runtime["runtime_controller"])
    controller = Path(str(controller_binding["path"])).resolve()
    checkpoint_binding = dict(release["algorithm_c_checkpoint"])
    if (
        controller_binding.get("sha256") != sha(controller)
        or Path(str(checkpoint_binding["path"])).resolve() != args.checkpoint
        or checkpoint_binding.get("sha256") != sha(args.checkpoint)
        or runtime.get("fixed_real_top_k") != 5
        or runtime.get("target_accepted_binary_shrinks") != 10
        or runtime.get("qps_resource_prediction_or_score_role") is not False
        or runtime.get("model_only_hard_pruning") is not False
    ):
        raise RuntimeError("Algorithm B executable release authority differs")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh Algorithm B output exists: {output}")
    log_root = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
    log_root.mkdir(parents=True, exist_ok=True)
    stdout_path = log_root / f"{args.run_id}.stdout.log"
    stderr_path = log_root / f"{args.run_id}.stderr.log"
    unit = f"mysql-four-scene-algb-{args.run_id}.service"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        "--description=MySQL four-scene formal Algorithm B from C",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout_path}",
        f"--property=StandardError=append:{stderr_path}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(controller), "--run-id", args.run_id,
        "--algorithm-b-release", str(args.algorithm_b_release),
        "--checkpoint", str(args.checkpoint),
        "--runtime-preflight", str(args.runtime_preflight),
        "--prepared-binding", str(args.prepared_binding),
        "--benchmark-accounts", str(args.benchmark_accounts),
        "--fresh-r0", str(args.fresh_r0),
        "--replay-quarantine", str(args.replay_quarantine),
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True,
        check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"Algorithm B systemd launch failed: {started.stderr[-1000:]}")

    state_path = output / "run_state.json"
    deadline = time.monotonic() + 90.0
    state: dict[str, Any] | None = None
    main_pid = 0
    while time.monotonic() < deadline:
        fields = unit_fields(unit, "ActiveState", "SubState", "MainPID", "Result")
        main_pid = int(fields.get("MainPID", "0"))
        if fields.get("ActiveState") in {"failed", "inactive"}:
            raise RuntimeError(
                f"Algorithm B controller exited during startup: {fields}; stderr={stderr_path}"
            )
        if state_path.is_file():
            state = load(state_path)
            if (
                main_pid > 0
                and state.get("status") == "RUNNING"
                and state.get("algorithm") == "B"
                and state.get("active_scenes") == SCENES
                and state.get("accepted_rounds") == {scene: 0 for scene in SCENES}
                and state.get("algorithm_b_release_sha256") == sha(args.algorithm_b_release)
            ):
                break
        time.sleep(0.25)
    else:
        stop(unit)
        raise RuntimeError("Algorithm B did not publish its fresh RUNNING state in 90s")
    assert state is not None

    watchdog_unit = f"mysql-four-scene-algb-watchdog-{args.run_id}.service"
    watchdog_output = output / "runtime_external_interference_watchdog.jsonl"
    watchdog_started = subprocess.run([
        "systemd-run", "--user",
        f"--unit={watchdog_unit.removesuffix('.service')}",
        "--description=MySQL Algorithm B non-mutating interference watchdog",
        f"--property=WorkingDirectory={ROOT}",
        "--property=KillMode=control-group",
        str(PYTHON), str(WATCHDOG), "--formal-unit", unit,
        "--formal-run-id", args.run_id, "--output", str(watchdog_output),
        "--interval-seconds", "1",
    ], cwd=ROOT, text=True, capture_output=True, check=False, timeout=30)
    if watchdog_started.returncode != 0:
        stop(unit)
        raise RuntimeError(
            "Algorithm B watchdog launch failed; controller stopped fail closed: "
            + watchdog_started.stderr[-1000:]
        )
    watchdog_pid = 0
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        fields = unit_fields(watchdog_unit, "ActiveState", "SubState", "MainPID")
        watchdog_pid = int(fields.get("MainPID", "0"))
        if fields.get("ActiveState") == "active" and watchdog_pid > 0:
            break
        if fields.get("ActiveState") in {"failed", "inactive"}:
            stop(unit)
            raise RuntimeError(f"Algorithm B watchdog exited during startup: {fields}")
        time.sleep(0.25)
    else:
        stop(unit)
        raise RuntimeError("Algorithm B watchdog did not become active in 10s")

    receipt = {
        "schema_version": "mysql_four_scene_algorithm_b_launch_receipt_v1",
        "status": "STARTED",
        "verified_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_evidence_eligible": True,
        "active_scenes": SCENES,
        "algorithm": "B",
        "method": "outer_binary_inner_estimated_coordinate_grid",
        "pid": main_pid,
        "systemd_unit": unit,
        "runtime_watchdog_unit": watchdog_unit,
        "runtime_watchdog_pid": watchdog_pid,
        "runtime_watchdog_output": str(watchdog_output),
        "external_process_mutation_performed": False,
        "external_affinity_guard_launched": False,
        "algorithm_b_release": {
            "path": str(args.algorithm_b_release),
            "sha256": sha(args.algorithm_b_release),
        },
        "algorithm_c_checkpoint": {
            "path": str(args.checkpoint), "sha256": sha(args.checkpoint),
        },
        "controller_source": {"path": str(controller), "sha256": sha(controller)},
        "run_state_path": str(state_path),
        "run_state_sha256_at_verification": sha(state_path),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    atomic(output / "launch_receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
