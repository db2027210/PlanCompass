#!/usr/bin/env python3
"""Unit checks for rootless worker launch-profile refresh."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

import double_loop_rootless_vm_task_worker as worker


def main() -> int:
    spec = {
        "task_id": "a13_r1",
        "target": "memory",
        "benchmark": "hybench_ap",
        "profile": {
            "cpu_cores": 10,
            "memory_mib": 1312,
            "read_mb": 800,
            "write_mb": 800,
            "launcher_args": ["--hybench-ap-shared-buffers-mb", "960"],
        },
    }
    current = {
        "profiles": {
            "memory:hybench_ap": {
                "cpu_cores": 10,
                "memory_mib": 1312,
                "read_mb": 800,
                "write_mb": 800,
                "launcher_args": ["--hybench-ap-shared-buffers-mb", "768"],
            }
        },
        "task_profile_overrides": {
            "a13_r1": {
                "launcher_args": [
                    "--resume-calibration-dir",
                    "/tmp/a13/calibration",
                ]
            }
        },
    }
    with tempfile.TemporaryDirectory() as raw:
        config = Path(raw) / "scheduler.json"
        config.write_text(json.dumps(current), encoding="utf-8")
        audit = worker.refresh_effective_profile(spec, config)
    assert audit["refreshed"] is True
    assert audit["original_profile"]["launcher_args"][-1] == "960"
    assert audit["effective_profile"]["launcher_args"][-1] == "/tmp/a13/calibration"
    assert spec["profile"]["launcher_args"][-1] == "/tmp/a13/calibration"
    assert audit["task_profile_override"] == current["task_profile_overrides"]["a13_r1"]

    captured = {}
    original_run = worker.run

    class FakeCompleted:
        stdout = "mysql.service enabled=masked active=failed\n"

    def fake_run(command, *, timeout):
        captured["command"] = command
        captured["timeout"] = timeout
        return FakeCompleted()

    worker.run = fake_run
    try:
        services = worker.quiesce_unrelated_database_services(22711)
    finally:
        worker.run = original_run
    assert captured["command"][0:3] == ["ssh", "-p", "22711"]
    assert "mysql.service mariadb.service" in captured["command"][-1]
    assert services["services"] == ["mysql.service enabled=masked active=failed"]

    qmp_call = {}
    original_qmp_execute = worker.qmp_execute

    def fake_qmp_execute(path, command, arguments=None):
        qmp_call.update({"path": path, "command": command, "arguments": arguments})
        return {"return": {}}

    worker.qmp_execute = fake_qmp_execute
    try:
        throttle = worker.set_qemu_disk_throttle(
            Path("/tmp/formal.qmp"),
            read_bps=5 * 1024 * 1024,
            write_bps=1024 * 1024 // 10,
        )
    finally:
        worker.qmp_execute = original_qmp_execute
    assert throttle["status"] == "ok"
    assert qmp_call["command"] == "block_set_io_throttle"
    assert qmp_call["arguments"]["device"] == "drive0"
    assert qmp_call["arguments"]["bps_rd_max"] == 5 * 1024 * 1024

    missing = worker.refresh_effective_profile(spec, Path("/missing/scheduler.json"))
    assert missing["refreshed"] is False
    assert "refresh_error" in missing
    assert spec["profile"]["launcher_args"][-1] == "/tmp/a13/calibration"

    # Cleanup must target the attempt-local QEMU PID directly.  A slot-scoped
    # QMP socket can already belong to the next attempt when an old worker is
    # still unwinding, so stop_vm must never connect to it.
    process = subprocess.Popen(
        ["bash", "-c", "exec -a qemu-system-x86_64 sleep 60"],
    )
    original_qmp_execute = worker.qmp_execute

    def forbidden_qmp(*args, **kwargs):
        raise AssertionError("stop_vm must not use a reusable QMP socket")

    worker.qmp_execute = forbidden_qmp
    try:
        for _ in range(100):
            command = Path(f"/proc/{process.pid}/cmdline").read_bytes()
            if b"qemu-system" in command:
                break
            time.sleep(0.01)
        worker.stop_vm(process.pid, Path("/tmp/reused-slot.qmp"))
        assert process.wait(timeout=5) == -15
    finally:
        worker.qmp_execute = original_qmp_execute
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    print("double-loop rootless worker profile refresh tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
