#!/usr/bin/env python3
"""0949: sampler accelerations must be opt-in; CPU configs stay on legacy path."""
from __future__ import annotations

import inspect
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_real_validate_candidates import vm_runtime_monitor_options
from system_resource_monitor import (
    _read_vm_runtime_snapshot,
    _run_ssh_command,
    monitor_resources_from_vm_runtime_workload,
)


def main() -> None:
    # Defaults on monitor options: all accelerations off.
    defaults = vm_runtime_monitor_options(
        {
            "ssh_target": "anonymous@192.0.2.10",
            "cpu_mode": "vm_busy",
            "io_mode": "disk_device",
            "memory_mode": "vm_used",
            "disk_devices": ["vda"],
            "interval_sec": 1.0,
        }
    )
    assert defaults["ssh_control_master"] is False, defaults
    assert defaults["light_remote_script"] is False, defaults
    assert defaults["interval_correct_sleep"] is False, defaults

    # Explicit IO opt-in config enables new path.
    io_opt = vm_runtime_monitor_options(
        {
            "ssh_target": "anonymous@192.0.2.13",
            "cpu_mode": "vm_busy",
            "io_mode": "disk_device",
            "memory_mode": "vm_used",
            "disk_devices": ["vda"],
            "interval_sec": 1.0,
            "ssh_control_master": True,
            "light_remote_script": True,
            "interval_correct_sleep": True,
        }
    )
    assert io_opt["ssh_control_master"] is True
    assert io_opt["light_remote_script"] is True
    assert io_opt["interval_correct_sleep"] is True

    # Function signatures default to legacy-safe False.
    mon_sig = inspect.signature(monitor_resources_from_vm_runtime_workload)
    assert mon_sig.parameters["ssh_control_master"].default is False
    assert mon_sig.parameters["light_remote_script"].default is False
    assert mon_sig.parameters["interval_correct_sleep"].default is False
    snap_sig = inspect.signature(_read_vm_runtime_snapshot)
    assert snap_sig.parameters["ssh_control_master"].default is False
    assert snap_sig.parameters["light_remote_script"].default is False
    ssh_sig = inspect.signature(_run_ssh_command)
    assert ssh_sig.parameters["ssh_control_master"].default is False

    # String/bool coercion for config JSON friendliness.
    truthy = vm_runtime_monitor_options({"ssh_control_master": "true"})
    falsy = vm_runtime_monitor_options({"ssh_control_master": "0"})
    assert truthy["ssh_control_master"] is True
    assert falsy["ssh_control_master"] is False

    print("vm runtime sampler opt-in isolation tests passed", flush=True)


if __name__ == "__main__":
    main()
