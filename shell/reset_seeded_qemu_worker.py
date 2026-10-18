#!/usr/bin/env python3
"""Reset one owned replay VM by replacing its disposable work overlay."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    from .provision_double_loop_matrix_direct_qemu import (
        DIRECT_ROOT,
        direct_pid_path,
        direct_running,
        launch_direct,
        read_root_file,
        wait_ready,
    )
    from .provision_double_loop_matrix_vms import VmSpec, run
except ImportError:
    from provision_double_loop_matrix_direct_qemu import (
        DIRECT_ROOT,
        direct_pid_path,
        direct_running,
        launch_direct,
        read_root_file,
        wait_ready,
    )
    from provision_double_loop_matrix_vms import VmSpec, run


def spec_from_reset(reset: Mapping[str, Any]) -> VmSpec:
    return VmSpec(
        exp_id=str(reset["exp_id"]),
        repeat_id=int(reset["repeat_id"]),
        benchmark=str(reset["benchmark"]),
        target=str(reset["target"]),
        name=str(reset["vm_name"]),
        mac=str(reset["mac"]),
        cpu=int(reset["cpu"]),
        memory_mib=int(reset["memory_mib"]),
        read_mb=float(reset["read_mb"]),
        write_mb=float(reset["write_mb"]),
        overlay_root=str(Path(str(reset["work_overlay"])).parent),
    )


def stop_work_guest(spec: VmSpec, timeout_s: float) -> None:
    if not direct_running(spec):
        return
    pid = int(read_root_file(direct_pid_path(spec)))
    run(["sudo", "-n", "kill", "-TERM", str(pid)], check=False)
    # The work layer is disposable and is unlinked immediately after this
    # process exits.  Give QEMU a short opportunity to terminate normally,
    # then kill only this exact namespaced PID.  Waiting 60 seconds here made
    # every Top-K replay needlessly serial and a successful SIGKILL was
    # previously reported as an error.
    deadline = time.time() + min(5.0, max(1.0, timeout_s))
    while time.time() < deadline:
        check = run(["sudo", "-n", "kill", "-0", str(pid)], check=False)
        if check.returncode != 0:
            return
        time.sleep(1.0)
    run(["sudo", "-n", "kill", "-KILL", str(pid)], check=False)
    deadline = time.time() + 10.0
    while time.time() < deadline:
        check = run(["sudo", "-n", "kill", "-0", str(pid)], check=False)
        if check.returncode != 0:
            return
        time.sleep(0.25)
    raise RuntimeError(f"direct QEMU PID remained alive after SIGKILL: {spec.name} pid={pid}")


def set_workload_io_throttle(spec: VmSpec) -> Dict[str, int]:
    """Switch a ready owned guest from boot speed to measured workload limits."""
    read_bps = int(float(spec.read_mb) * 1024 * 1024)
    write_bps = int(float(spec.write_mb) * 1024 * 1024)
    qmp_path = DIRECT_ROOT / f"{spec.name}.qmp"
    requests = [
        {"execute": "qmp_capabilities"},
        {
            "execute": "block_set_io_throttle",
            "arguments": {
                "device": "drive0",
                "bps": 0,
                "bps_rd": read_bps,
                "bps_wr": write_bps,
                "iops": 0,
                "iops_rd": 0,
                "iops_wr": 0,
            },
        },
        {"execute": "query-block"},
    ]
    payload = "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in requests)
    completed = subprocess.run(
        [
            "sudo",
            "-n",
            "timeout",
            "5",
            "socat",
            "-",
            f"UNIX-CONNECT:{qmp_path}",
        ],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"QMP IO throttle failed for {spec.name}: "
            f"exit={completed.returncode} output={completed.stdout}"
        )
    responses = []
    for line in completed.stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            responses.append(value)
    errors = [item["error"] for item in responses if "error" in item]
    if errors:
        raise RuntimeError(f"QMP IO throttle rejected for {spec.name}: {errors}")
    block_rows = [
        row
        for item in responses
        for row in (item.get("return") if isinstance(item.get("return"), list) else [])
        if isinstance(row, dict) and row.get("device") == "drive0"
    ]
    inserted = dict(block_rows[-1].get("inserted") or {}) if block_rows else {}
    actual_read_bps = int(inserted.get("bps_rd") or -1)
    actual_write_bps = int(inserted.get("bps_wr") or -1)
    if actual_read_bps != read_bps or actual_write_bps != write_bps:
        raise RuntimeError(
            f"QMP IO throttle verification failed for {spec.name}: "
            f"expected=({read_bps},{write_bps}) "
            f"actual=({actual_read_bps},{actual_write_bps})"
        )
    return {"read_bps": actual_read_bps, "write_bps": actual_write_bps}


def reset_seeded_worker(
    reset: Mapping[str, Any],
    *,
    expected_ip: str,
    timeout_s: float,
) -> Dict[str, Any]:
    started = time.time()
    spec = spec_from_reset(reset)
    seed = Path(str(reset["seed_overlay"])).resolve()
    work = Path(str(reset["work_overlay"])).resolve()
    if work != spec.overlay.resolve():
        raise RuntimeError(f"seeded reset work-overlay/spec mismatch: {work} != {spec.overlay}")
    if not seed.is_file():
        raise FileNotFoundError(f"seeded reset image is missing: {seed}")
    stop_work_guest(spec, timeout_s)
    run(["sudo", "-n", "unlink", str(work)], check=False)
    # Boot is outside the measured workload.  Starting a heavily throttled IO
    # guest at its final limit can dominate every Top-K replay, so boot the same
    # disposable layer at seed speed, then atomically restore the requested
    # physical limit before sync/drop-cache and resource monitoring begin.
    boot_spec = replace(
        spec,
        read_mb=max(800.0, float(spec.read_mb)),
        write_mb=max(800.0, float(spec.write_mb)),
    )
    launch = launch_direct(boot_spec, seed)
    ready = wait_ready([boot_spec], max(30.0, timeout_s))[spec.name]
    throttle = set_workload_io_throttle(spec)
    actual_ip = str(ready["ip"])
    if expected_ip and actual_ip != expected_ip:
        raise RuntimeError(
            f"seeded reset DHCP identity changed for {spec.name}: "
            f"expected={expected_ip}, actual={actual_ip}"
        )
    return {
        "worker_id": spec.name,
        "host": actual_ip,
        "database": str(reset.get("database") or ""),
        "template_database": str(reset.get("template_database") or ""),
        "elapsed_s": time.time() - started,
        "status": "ok",
        "strategy": "immutable_per_vm_seed_plus_replaceable_work_overlay",
        "seed_overlay": str(seed),
        "work_overlay": str(work),
        "qemu_pid": launch.get("pid"),
        "boot_read_mb": boot_spec.read_mb,
        "boot_write_mb": boot_spec.write_mb,
        "workload_read_bps": throttle["read_bps"],
        "workload_write_bps": throttle["write_bps"],
    }
