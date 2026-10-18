#!/usr/bin/env python3
"""Provision one exact owned matrix VM and launch its formal double-loop run."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List


ROOT = Path(__file__).resolve().parents[1]
SUDO_COMMAND_TIMEOUT_S = 300.0
SEED_REBUILD_TIMEOUT_S = 18000.0


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(command: List[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    print(completed.stdout, end="", flush=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed exit={completed.returncode}: {' '.join(command)}\n{completed.stdout}"
        )
    return completed


def newest_launch(root: Path) -> Path:
    paths = sorted(root.glob("formal_launch_*.json"), key=lambda path: path.stat().st_mtime)
    if not paths:
        raise RuntimeError(f"formal launcher produced no manifest under {root}")
    return paths[-1]


def process_active(pid: int) -> bool:
    """Return whether a PID still represents a live, non-zombie process."""
    try:
        status = Path(f"/proc/{int(pid)}/status").read_text(
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
        return False
    state_line = next(
        (line for line in status.splitlines() if line.startswith("State:")),
        "",
    )
    parts = state_line.split()
    state = parts[1].upper() if len(parts) >= 2 else ""
    return state not in {"Z", "X"}


def stop_exact_owned_vm(vm_name: str) -> None:
    """Stop only a scheduler-owned exact namespace before a seed resume."""
    completed = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    matches = []
    needle = f"-name {vm_name} "
    for line in completed.stdout.splitlines():
        stripped = line.strip()
        if "qemu-system" not in stripped or needle not in stripped:
            continue
        pid_raw, _, command = stripped.partition(" ")
        if command and needle in command:
            matches.append(int(pid_raw))
    if len(matches) > 1:
        raise RuntimeError(f"multiple exact QEMU processes for {vm_name}: {matches}")
    if not matches:
        return
    pid = matches[0]
    subprocess.run(
        ["sudo", "-n", "kill", "-TERM", str(pid)],
        timeout=SUDO_COMMAND_TIMEOUT_S,
        check=False,
    )
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not process_active(pid):
            return
        time.sleep(0.5)
    subprocess.run(
        ["sudo", "-n", "kill", "-KILL", str(pid)],
        timeout=SUDO_COMMAND_TIMEOUT_S,
        check=False,
    )


def stop_stale_task_processes(task_id: str) -> List[int]:
    """Stop only stale user processes carrying this formal task namespace."""
    completed = subprocess.run(
        ["ps", "-eo", "pid=,uid=,args="],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
        check=False,
    )
    current_uid = os.getuid()
    current_pid = os.getpid()
    markers = (f"/{task_id}/", f"/{task_id}_", f"/{task_id}-")
    roots = (
        "/resource_adaptive_experiment_matrix_20260718/",
        "/scheduled_18/",
    )
    matches: List[int] = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, uid = int(fields[0]), int(fields[1])
        command = fields[2]
        if pid == current_pid or uid != current_uid:
            continue
        if any(root in command for root in roots) and any(marker in command for marker in markers):
            matches.append(pid)
    for pid in matches:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
    deadline = time.time() + 10.0
    remaining = set(matches)
    while remaining and time.time() < deadline:
        remaining = {pid for pid in remaining if process_active(pid)}
        if remaining:
            time.sleep(0.25)
    for pid in sorted(remaining):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return sorted(matches)


def reusable_seed(spec: Dict[str, Any]) -> bool:
    """Require semantic database proof before reusing non-HyBench seeds."""
    seed = Path(spec["seed_overlay"]).resolve()
    if not seed.is_file():
        return False
    benchmark = str(spec["benchmark"])
    if benchmark.startswith("hybench_"):
        return True
    audit_paths = [seed.parent / f"{spec['vm_name']}.database_bootstrap.json"]
    # The seed directory can be root-owned because QEMU was launched through
    # sudo.  The per-attempt audit is user-owned and is equally authoritative;
    # scan all attempts so a completed orphan loader is never repeated merely
    # because its parent rebuild process exited before the final privileged
    # copy.
    if spec.get("task_root"):
        task_root = Path(spec["task_root"]).resolve()
        audit_paths.extend(
            sorted(
                task_root.parent.glob(
                    f"attempt_*/seed_database_bootstrap/{spec['vm_name']}/database_bootstrap_audit.json"
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
    for audit_path in audit_paths:
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if (
            audit.get("status") == "ok"
            and str(audit.get("benchmark")) == benchmark
            and int(((audit.get("verification") or {}).get("user_table_count") or 0)) > 0
            and int(((audit.get("verification") or {}).get("template_size_bytes") or 0)) > 0
        ):
            return True
    return False


def repairable_seed(spec: Dict[str, Any]) -> bool:
    """Recognize a fully loaded generic seed whose template clone alone failed."""
    seed = Path(spec["seed_overlay"]).resolve()
    benchmark = str(spec["benchmark"])
    if not seed.is_file() or benchmark not in {"job", "tpch", "tpcc"} or not spec.get("task_root"):
        return False
    task_root = Path(spec["task_root"]).resolve()
    audit_paths = sorted(
        task_root.parent.glob(
            f"attempt_*/seed_database_bootstrap/{spec['vm_name']}/database_bootstrap_audit.json"
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for audit_path in audit_paths:
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        verification = audit.get("verification") or {}
        if (
            audit.get("status") == "failed"
            and str(audit.get("benchmark")) == benchmark
            and int(verification.get("user_table_count") or 0) > 0
            and int(verification.get("database_size_bytes") or 0) > 0
            and int(verification.get("template_size_bytes") or 0) <= 0
            and "template creation refused" in str(audit.get("error") or "").lower()
        ):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch-spec", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = time.time()
    spec = json.loads(args.launch_spec.read_text(encoding="utf-8"))
    result: Dict[str, Any] = {
        "task_id": spec["task_id"],
        "status": "failed",
        "started_at": started,
    }
    try:
        task_root = Path(spec["task_root"]).resolve()
        task_root.mkdir(parents=True, exist_ok=True)
        result["stale_task_processes_stopped"] = stop_stale_task_processes(str(spec["task_id"]))
        seed = Path(spec["seed_overlay"]).resolve()
        can_resume_seed = reusable_seed(spec)
        can_repair_seed = not can_resume_seed and repairable_seed(spec)
        if seed.is_file():
            stop_exact_owned_vm(str(spec["vm_name"]))
        rebuild = [
            sys.executable,
            "-u",
            str(ROOT / "shell/rebuild_matrix_seeded_qemu.py"),
            "--root",
            str(task_root),
            "--prepared",
            str(Path(spec["prepared_image"]).resolve()),
            "--only",
            str(spec["selector"]),
            "--overlay-root",
            str(Path(spec["overlay_root"]).resolve()),
            "--cpu-cores",
            str(int(spec["profile"]["cpu_cores"])),
            "--memory-mib",
            str(int(spec["profile"]["memory_mib"])),
            "--read-mb",
            str(float(spec["profile"]["read_mb"])),
            "--write-mb",
            str(float(spec["profile"]["write_mb"])),
        ]
        if can_resume_seed:
            rebuild.append("--resume-from-seeds")
        elif can_repair_seed:
            rebuild.append("--repair-generic-template")
        # Loading a clean TPC-H seed can legitimately exceed 40 minutes when
        # several independent VMs share the host.  This is an infrastructure
        # deadline only; it does not reduce optimizer rounds or search size.
        run(rebuild, timeout=SEED_REBUILD_TIMEOUT_S)

        launch = [
            sys.executable,
            "-u",
            str(ROOT / "shell/launch_double_loop_formal_matrix.py"),
            "--root",
            str(task_root),
            "--only",
            str(spec["task_id"]),
            "--attempt-tag",
            f"scheduled_a{int(spec['attempt'])}",
        ]
        launch.extend(str(item) for item in spec["profile"].get("launcher_args") or [])
        run(launch, timeout=300)

        launch_manifest = newest_launch(task_root)
        payload = json.loads(launch_manifest.read_text(encoding="utf-8"))
        rows = payload.get("rows") or []
        row = next(
            (
                item
                for item in rows
                if str(item.get("exp_id")) == str(spec["exp_id"])
                and int(item.get("repeat_id") or 0) == int(spec["repeat_id"])
            ),
            None,
        )
        if not isinstance(row, dict) or row.get("status") not in {
            "launched",
            "already_running",
            "already_finished",
        }:
            raise RuntimeError(f"formal launcher returned no usable row: {row}")
        result.update(
            {
                "status": "launched" if row.get("status") != "already_finished" else "finished",
                "runner_pid": row.get("pid"),
                "run_dir": row.get("run_dir"),
                "runtime_dir": row.get("runtime_dir"),
                "vm_name": row.get("vm_name"),
                "vm_ip": row.get("vm_ip"),
                "launch_manifest": str(launch_manifest),
                "worker_elapsed_s": time.time() - started,
            }
        )
    except Exception as exc:  # noqa: BLE001 - persisted worker boundary
        result.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
                "worker_elapsed_s": time.time() - started,
            }
        )
        write_json(args.result, result)
        return 1
    write_json(args.result, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
