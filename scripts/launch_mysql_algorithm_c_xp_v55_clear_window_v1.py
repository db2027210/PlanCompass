#!/usr/bin/env python3
"""Launch both saved XP Algorithm C lanes at the first clean process window."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_v55.py"
OBSERVER = ROOT / "scripts/watch_mysql_algorithm_c_async_noninterference_v1.py"
MANAGEMENT_CPUSET = "240-241,496-497"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_v6 as preflight
from scripts import run_mysql_algorithm_c_async_scene_v55 as controller
from scripts import watch_mysql_algorithm_c_async_noninterference_v1 as observer


EXTERNAL_IDS = (
    "d945b08e1a2ad736a2c8ada4704f7123165ea8dc4c41ed3fcfeaf1d3b4d718bb",
    "4e0f56d16ce0397222c58236b8182c35ea41189bd9a8f776122fe0da135fc70d",
)
SCENES = {
    "hybench_xp_cpu": {
        "suffix": "cpu",
        "checkpoint": HISTORY / "v54_to_c_xp_top4_20260825_attempt008_cpu_polluted_outer5_checkpoint_v3/prepared_binding.json",
        "runtime_preflight": ROOT / "preflight/mysql_four_scene_20260809/v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json",
        "prepared_binding": HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001/prepared_binding.json",
        "benchmark_accounts": HISTORY / "v29_prelaunch_xp_recovery_20260819_attempt001/benchmark_accounts_binding.json",
        "fresh_r0": HISTORY / "v36_to_c_per_scene_20260819_attempt037_xp_cpu_r0_recovered/r0_binding.json",
        "replay_quarantine": HISTORY / "v54_to_c_xp_top4_20260825_attempt008_cpu_polluted_outer5_quarantine_v1/replay_quarantine.json",
    },
    "hybench_xp_io": {
        "suffix": "io",
        "checkpoint": HISTORY / "v54_to_c_xp_top4_20260825_attempt005_io_discard_outer2_checkpoint/prepared_binding.json",
        "runtime_preflight": ROOT / "preflight/mysql_four_scene_20260809/v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json",
        "prepared_binding": HISTORY / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery/prepared_binding.json",
        "benchmark_accounts": HISTORY / "v36_to_c_per_scene_20260819_attempt040_xp_io_w2_live_recovery/benchmark_accounts_binding.json",
        "fresh_r0": HISTORY / "v36_to_c_per_scene_20260819_attempt044_xp_io_r0_recovered/r0_binding.json",
        "replay_quarantine": HISTORY / "v54_to_c_xp_top4_20260825_attempt006_io_discard_outer2_quarantine/replay_quarantine.json",
    },
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def clear_snapshot() -> tuple[bool, dict[str, Any]]:
    scans = [preflight._container_process_scan(value) for value in EXTERNAL_IDS]
    database_processes = [
        preflight._database_workload_processes(scan) for scan in scans
    ]
    heavyweight = observer.heavyweight_external_processes()
    # Database containers are inventoried here but classified by the formal
    # v55 preflight (stable inspected co-resident vs restarting process).  The
    # launcher itself gates only the known full-machine BFS workload; requiring
    # every stable co-resident database to have zero processes would prevent
    # any formal launch indefinitely.
    passed = all(scan["scan_complete"] for scan in scans) and not heavyweight
    return passed, {
        "captured_at": now(), "scans": scans,
        "database_workload_processes": database_processes,
        "heavyweight_external_processes": heavyweight,
        "passed": passed,
    }


def unit_fields(unit: str) -> dict[str, str]:
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "Result", "-p", "ExecMainStatus"],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=10,
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def start_scene(base_run_id: str, scene: str, config: dict[str, Any]) -> dict[str, Any]:
    run_id = f"{base_run_id}_{config['suffix']}_xp_{config['suffix']}"
    output = HISTORY / run_id
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh output required: {output}")
    barrier = Path("/tmp/mysql_async_outer_start") / run_id / "algorithm_c"
    if barrier.exists() or barrier.is_symlink():
        raise RuntimeError(f"fresh barrier required: {barrier}")
    unit = f"mysql-algc-async-{run_id}.service"
    stdout = LOG_ROOT / f"{run_id}.stdout.log"
    stderr = LOG_ROOT / f"{run_id}.stderr.log"
    command = [
        "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
        f"--description=MySQL Algorithm C v55 independent scene {scene}",
        f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
        f"--property=StandardOutput=append:{stdout}",
        f"--property=StandardError=append:{stderr}",
        "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET, str(PYTHON),
        str(CONTROLLER), "--run-id", run_id,
        "--checkpoint", str(config["checkpoint"]),
        "--runtime-preflight", str(config["runtime_preflight"]),
        "--prepared-binding", str(config["prepared_binding"]),
        "--benchmark-accounts", str(config["benchmark_accounts"]),
        "--fresh-r0", str(config["fresh_r0"]),
        "--replay-quarantine", str(config["replay_quarantine"]),
        "--scene", scene, "--scene-start-barrier", str(barrier),
    ]
    started = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if started.returncode != 0:
        raise RuntimeError(f"start failed for {scene}: {started.stderr[-1000:]}")
    return {
        "scene": scene, "run_id": run_id, "unit": unit,
        "output": str(output), "stdout": str(stdout), "stderr": str(stderr),
    }


def start_observer(row: dict[str, Any]) -> str:
    unit = f"mysql-algc-observer-{row['run_id']}.service"
    output = Path(row["output"]) / "runtime_noninterference_observer.jsonl"
    result = subprocess.run(
        [
            "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
            f"--description=MySQL Algorithm C v55 observer {row['scene']}",
            f"--property=WorkingDirectory={ROOT}", "--property=KillMode=control-group",
            str(PYTHON), str(OBSERVER), "--formal-unit", row["unit"],
            "--formal-run-id", row["run_id"], "--output", str(output),
            "--interval-seconds", "1",
        ],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"observer start failed: {result.stderr[-1000:]}")
    return unit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--wait-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    if args.wait_seconds <= 0:
        parser.error("positive wait required")
    controller.validate_contract()
    for config in SCENES.values():
        for key, value in config.items():
            if key != "suffix" and (not Path(value).is_file() or Path(value).is_symlink()):
                raise RuntimeError(f"bound input missing: {value}")
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.wait_seconds
    proof = None
    while time.monotonic() < deadline:
        first_pass, first = clear_snapshot()
        if first_pass:
            time.sleep(0.25)
            second_pass, second = clear_snapshot()
            if second_pass:
                proof = {"first": first, "second": second, "passed": True}
                break
        time.sleep(0.5)
    if proof is None:
        raise RuntimeError("no two-scan clean external database process window")

    rows = [start_scene(args.run_id, scene, config) for scene, config in SCENES.items()]
    startup_deadline = time.monotonic() + 120.0
    pending = {row["run_id"] for row in rows}
    while pending and time.monotonic() < startup_deadline:
        for row in rows:
            if row["run_id"] not in pending:
                continue
            state_path = Path(row["output"]) / "run_state.json"
            if state_path.is_file() and not state_path.is_symlink():
                pending.remove(row["run_id"])
                continue
            fields = unit_fields(row["unit"])
            if fields.get("ActiveState") in {"inactive", "failed"}:
                raise RuntimeError(
                    f"{row['scene']} exited before creating run_state: {fields}"
                )
        if pending:
            time.sleep(0.25)
    if pending:
        raise RuntimeError(f"XP controllers did not create run_state: {sorted(pending)}")
    for row in rows:
        row["observer_unit"] = start_observer(row)
        row["unit_fields"] = unit_fields(row["unit"])
    manifest = LOG_ROOT / f"{args.run_id}.xp_v55_clear_window_launch.json"
    temporary = manifest.with_name(f".{manifest.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps({
        "schema_version": "mysql_algorithm_c_xp_v55_clear_window_launch_v1",
        "captured_at": now(), "clear_window_proof": proof, "scenes": rows,
        "cross_scene_completion_dependency": False,
        "external_process_mutation": False,
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)
    print(json.dumps({"manifest": str(manifest), "scenes": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
