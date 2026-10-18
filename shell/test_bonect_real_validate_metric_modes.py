#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.bonect_real_validate_candidates import resolve_runtime_capability, vm_runtime_monitor_options


def main() -> None:
    options = vm_runtime_monitor_options(
        {
            "ssh_target": "anonymous@192.0.2.10",
            "cpu_mode": "vm_busy",
            "io_mode": "disk_device",
            "memory_mode": "vm_used",
            "disk_devices": ["vda"],
            "max_runtime_s": 305,
            "fixed_horizon_success": True,
        }
    )
    assert options["cpu_mode"] == "vm_busy"
    assert options["io_mode"] == "disk_device"
    assert options["memory_mode"] == "vm_used"
    assert options["disk_devices"] == ["vda"]
    assert options["max_runtime_s"] == 305
    assert options["fixed_horizon_success"] is True

    defaults = vm_runtime_monitor_options({})
    assert defaults["cpu_mode"] == "pg_proc"
    assert defaults["io_mode"] == "disk_device"
    assert defaults["memory_mode"] == "pg_proc_rss"
    assert defaults["max_runtime_s"] == 0
    assert defaults["fixed_horizon_success"] is False

    capability, basis = resolve_runtime_capability(
        {
            "capability": {"cpu": 13, "memory": 8.9, "io": 1600},
            "metrics": {"vm_runtime": {"io_capability_mode": "sum_directional_limits"}},
        },
        {"read_mb": 800, "write_mb": 800},
    )
    assert capability["io"] == 1600
    assert basis["expected_total_mib_s"] == 1600
    try:
        resolve_runtime_capability(
            {
                "capability": {"cpu": 13, "memory": 8.9, "io": 800},
                "metrics": {"vm_runtime": {"io_capability_mode": "sum_directional_limits"}},
            },
            {"read_mb": 800, "write_mb": 800},
        )
    except ValueError as exc:
        assert "IO capability mismatch" in str(exc), exc
    else:
        raise AssertionError("read+write IO capability mismatch must fail")
    print("bonect real validator metric mode tests passed", flush=True)


if __name__ == "__main__":
    main()
