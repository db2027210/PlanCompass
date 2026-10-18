#!/usr/bin/env python3
"""Recover the three reboot-interrupted Algorithm A lanes."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_reboot_resume_v1.py"
CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_reboot_recovery_20260821_v5"
    / "recovery_contract.json"
)
HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
SOURCE_MANIFEST = (
    LOG_ROOT
    / "v39_to_a_per_scene_20260820_attempt002.async_a.launch_manifest.json"
)
RECOVERY_ID = "v39_to_a_reboot_resume_20260821_attempt008"
CAMPAIGN_ID = "v39_to_a_per_scene_20260820_attempt002"
MANAGEMENT_CPUSET = "240-241,496-497"
PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809"
    / "v54_recovered_xp_rebind_r0_20260817_attempt012_preflight.json"
)
ACTIVE = {
    "job_memory": "job",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def validate_contract() -> dict[str, Any]:
    contract = load(CONTRACT)
    if (
        contract.get("status") != "FROZEN"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("contract")
        != "mysql_algorithm_a_host_reboot_same_cohort_resume_v5"
        or contract.get("same_two_worker_cohort_required") is not True
        or contract.get("discard_interrupted_partial_round") is not True
        or contract.get("repeat_interrupted_round_from_start") is not True
        or contract.get("post_convergence_stability_required") is not False
    ):
        raise RuntimeError("reboot recovery contract differs")
    for row in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(row["path"])
        if path.is_symlink() or not path.is_file() or sha(path) != row["sha256"]:
            raise RuntimeError(f"recovery source binding differs: {path}")
    return contract


def unit_fields(unit: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "systemctl", "--user", "show", unit,
            "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
            "-p", "Result", "-p", "ExecMainStatus",
        ],
        cwd=ROOT, text=True, capture_output=True, check=True, timeout=15,
    )
    return dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )


def build_snapshot(
    *, scene: str, source_path: Path, destination: Path, boot_id: str,
) -> dict[str, Any]:
    source = load(source_path)
    checkpoint = copy.deepcopy(source.get("round_checkpoint"))
    if (
        source.get("status") != "RUNNING"
        or source.get("algorithm") != "A"
        or source.get("async_scene") != scene
        or source.get("formal_scope") != "mysql_only"
        or not checkpoint
        or int(checkpoint.get("round_index") or 0)
        != int(source.get("round_index") or 0)
    ):
        raise RuntimeError(f"source is not a reboot-interrupted A lane: {scene}")
    snapshot = copy.deepcopy(source)
    snapshot.update({
        "schema_version": "mysql_algorithm_a_reboot_boundary_snapshot_v1",
        "status": "REBOOT_BOUNDARY_SNAPSHOT",
        "stage": "REBOOT_INTERRUPTED_PARTIAL_ROUND_ARCHIVED",
        "predecessor_run_id": source.get("run_id"),
        "predecessor_state_path": str(source_path),
        "predecessor_state_sha256": sha(source_path),
        "recovery_contract": (
            "mysql_algorithm_a_host_reboot_same_cohort_resume_v5"
        ),
        "reboot_boundary_snapshot": True,
        "recovery_cause": "host_reboot",
        "recovery_boot_id": boot_id,
        "snapshot_created_at": now(),
        "interrupted_round_index": int(source["round_index"]),
        "archived_interrupted_round_checkpoint": checkpoint,
        "archived_interrupted_round_checkpoint_sha256": canonical_sha(checkpoint),
        "partial_interrupted_round_credited": False,
        "round_checkpoint": None,
    })
    atomic(destination, snapshot)
    return snapshot


def start_containers(names: list[str]) -> dict[str, Any]:
    def running(name: str) -> bool:
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", name],
            cwd=ROOT, text=True, capture_output=True, check=False, timeout=20,
        )
        return inspected.returncode == 0 and inspected.stdout.strip() == "true"

    start_events: list[dict[str, Any]] = []
    for name in names:
        if running(name):
            start_events.append({"container": name, "already_running": True})
            continue
        process = subprocess.Popen(
            ["docker", "start", name], cwd=ROOT, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline and not running(name):
            if process.poll() is not None:
                break
            time.sleep(1.0)
        became_running = running(name)
        if process.poll() is None:
            # Docker can leave the CLI waiting even after the daemon has
            # started MySQL.  Ending that client does not stop the container.
            process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        start_events.append({
            "container": name,
            "already_running": False,
            "docker_start_returncode": process.returncode,
            "docker_start_stdout": stdout.strip(),
            "docker_start_stderr": stderr.strip(),
            "running": became_running,
        })
        if not became_running:
            raise RuntimeError(
                f"container did not start: {name}: {stderr[-1000:]}"
            )
    deadline = time.monotonic() + 180.0
    while time.monotonic() < deadline:
        if all(running(name) for name in names):
            return {"started": names, "start_events": start_events}
        time.sleep(2.0)
    raise RuntimeError("recovery containers did not remain running")


def main() -> int:
    contract = validate_contract()
    if not PREFLIGHT.is_file() or sha(PREFLIGHT) != (
        "ebff2a0843ab90c7ce31ccbf1f6b7784261a7cea79e87af3db5598ebd00ec25a"
    ):
        raise RuntimeError("runtime preflight binding differs")
    manifest = load(SOURCE_MANIFEST)
    if manifest.get("campaign_id") != CAMPAIGN_ID:
        raise RuntimeError("source campaign differs")
    recovery_root = HISTORY / RECOVERY_ID
    if recovery_root.exists() or recovery_root.is_symlink():
        raise RuntimeError(f"fresh recovery root required: {recovery_root}")
    recovery_root.mkdir(parents=True, mode=0o700)
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    snapshots: dict[str, Path] = {}
    for scene, suffix in ACTIVE.items():
        source_path = Path(str(manifest["scenes"][scene]["state"]))
        snapshot = recovery_root / f"{suffix}.reboot_boundary_snapshot.json"
        build_snapshot(
            scene=scene, source_path=source_path,
            destination=snapshot, boot_id=boot_id,
        )
        snapshots[scene] = snapshot

    freeze = load(Path(str(load(PREFLIGHT)["freeze_path"])))
    active_workers = [
        row for row in (freeze.get("workers") or []) if row.get("scene") in ACTIVE
    ]
    expected_ids = {
        "job_w1", "job_w2", "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
    }
    if {row.get("worker_id") for row in active_workers} != expected_ids:
        raise RuntimeError("recovery worker cohort differs")
    containers = [str(row["container"]) for row in active_workers]
    container_start = start_containers(containers)

    outputs = {
        scene: HISTORY / f"{RECOVERY_ID}_{suffix}"
        for scene, suffix in ACTIVE.items()
    }
    if any(path.exists() or path.is_symlink() for path in outputs.values()):
        raise RuntimeError("fresh recovery outputs required")
    units: dict[str, str] = {}
    logs: dict[str, dict[str, str]] = {}
    fields: dict[str, dict[str, str]] = {}
    launched: list[str] = []
    try:
        for scene, suffix in ACTIVE.items():
            source_row = manifest["scenes"][scene]
            lane_run_id = f"{RECOVERY_ID}_{suffix}"
            unit = f"mysql-alga-reboot-{lane_run_id}.service"
            stdout = LOG_ROOT / f"{lane_run_id}.stdout.log"
            stderr = LOG_ROOT / f"{lane_run_id}.stderr.log"
            command = [
                "systemd-run", "--user", f"--unit={unit.removesuffix('.service')}",
                f"--description=MySQL Algorithm A reboot recovery {scene}",
                f"--property=WorkingDirectory={ROOT}",
                "--property=KillMode=control-group",
                f"--property=StandardOutput=append:{stdout}",
                f"--property=StandardError=append:{stderr}",
                "/usr/bin/taskset", "-c", MANAGEMENT_CPUSET,
                str(PYTHON), str(CONTROLLER),
                "--run-id", lane_run_id,
                "--scene", scene,
                "--predecessor-state", str(snapshots[scene]),
                "--runtime-preflight", str(PREFLIGHT),
                "--prepared-binding", str(source_row["prepared_binding"]),
                "--benchmark-accounts", str(source_row["benchmark_accounts"]),
                "--fresh-r0", str(source_row["fresh_r0"]),
                "--output-dir", str(outputs[scene]),
            ]
            started = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True,
                check=False, timeout=30,
            )
            if started.returncode != 0:
                raise RuntimeError(f"{scene} launch failed: {started.stderr[-2000:]}")
            units[scene] = unit
            logs[scene] = {"stdout": str(stdout), "stderr": str(stderr)}
            launched.append(unit)

        deadline = time.monotonic() + 300.0
        states: dict[str, dict[str, Any]] = {}
        while time.monotonic() < deadline:
            ready = True
            for scene, unit in units.items():
                fields[scene] = unit_fields(unit)
                if fields[scene].get("ActiveState") in {"inactive", "failed"}:
                    raise RuntimeError(
                        f"{scene} exited during recovery startup: {fields[scene]}"
                    )
                state_path = outputs[scene] / "algorithm_a_state.json"
                if not state_path.is_file():
                    ready = False
                    continue
                states[scene] = load(state_path)
                if not (
                    states[scene].get("status") == "RUNNING"
                    and states[scene].get("algorithm") == "A"
                    and states[scene].get("async_scene") == scene
                    and states[scene].get("partial_interrupted_round_credited")
                    is False
                ):
                    ready = False
            if ready and len(states) == len(ACTIVE):
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Algorithm A reboot recovery startup timed out")
    except BaseException:
        for unit in launched:
            subprocess.run(
                ["systemctl", "--user", "stop", unit], cwd=ROOT,
                text=True, capture_output=True, check=False, timeout=30,
            )
        raise

    recovery_manifest = LOG_ROOT / f"{RECOVERY_ID}.async_a.recovery_manifest.json"
    scenes = copy.deepcopy(manifest["scenes"])
    for scene, suffix in ACTIVE.items():
        source_row = manifest["scenes"][scene]
        scenes[scene] = {
            **copy.deepcopy(source_row),
            "run_id": f"{RECOVERY_ID}_{suffix}",
            "unit": units[scene],
            "main_pid": int(fields[scene].get("MainPID") or 0),
            "output": str(outputs[scene]),
            "state": str(outputs[scene] / "algorithm_a_state.json"),
            "stage": states[scene].get("stage"),
            "predecessor_state": str(snapshots[scene]),
            "predecessor_state_sha256": sha(snapshots[scene]),
            **logs[scene],
        }
    atomic(recovery_manifest, {
        **copy.deepcopy(manifest),
        "schema_version": "mysql_algorithm_a_reboot_recovery_manifest_v1",
        "status": "LAUNCHED",
        "launched_at": now(),
        "campaign_id": CAMPAIGN_ID,
        "recovery_id": RECOVERY_ID,
        "recovery_cause": "host_reboot",
        "recovery_boot_id": boot_id,
        "recovery_contract_path": str(CONTRACT),
        "recovery_contract_sha256": sha(CONTRACT),
        "container_start": container_start,
        "interrupted_partial_rounds_credited": False,
        "tpch_terminal_state_preserved": True,
        "scenes": scenes,
    })
    print(json.dumps({
        "status": "LAUNCHED",
        "recovery_id": RECOVERY_ID,
        "manifest": str(recovery_manifest),
        "units": units,
        "stages": {scene: states[scene].get("stage") for scene in ACTIVE},
        "contract": contract["contract"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
