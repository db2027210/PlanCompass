#!/usr/bin/env python3
"""Regression checks for the isolated formal-matrix worker."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

from shell import double_loop_vm_task_worker as worker


def test_reusable_seed_requires_semantic_audit() -> None:
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        seed = root / "job-seed.qcow2"
        seed.touch()
        spec = {
            "seed_overlay": str(seed),
            "benchmark": "job",
            "vm_name": "job-seed",
        }
        assert not worker.reusable_seed(spec)

        audit = {
            "status": "ok",
            "benchmark": "job",
            "verification": {
                "user_table_count": 21,
                "template_size_bytes": 9_000_000_000,
            },
        }
        (root / "job-seed.database_bootstrap.json").write_text(
            json.dumps(audit),
            encoding="utf-8",
        )
        assert worker.reusable_seed(spec)

        audit["verification"]["template_size_bytes"] = 0
        (root / "job-seed.database_bootstrap.json").write_text(
            json.dumps(audit),
            encoding="utf-8",
        )
        assert not worker.reusable_seed(spec)

        (root / "job-seed.database_bootstrap.json").unlink()
        attempt_audit = (
            root
            / "runs"
            / "attempt_3"
            / "seed_database_bootstrap"
            / "job-seed"
            / "database_bootstrap_audit.json"
        )
        attempt_audit.parent.mkdir(parents=True)
        attempt_audit.write_text(
            json.dumps(
                {
                    **audit,
                    "verification": {
                        "user_table_count": 21,
                        "template_size_bytes": 9_000_000_000,
                    },
                }
            ),
            encoding="utf-8",
        )
        spec["task_root"] = str(root / "runs" / "attempt_4")
        assert worker.reusable_seed(spec)

        attempt_audit.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "benchmark": "job",
                    "verification": {
                        "user_table_count": 21,
                        "database_size_bytes": 9_000_000_000,
                    },
                    "error": "RuntimeError: template creation refused because source has 1 client session(s)",
                }
            ),
            encoding="utf-8",
        )
        assert not worker.reusable_seed(spec)
        assert worker.repairable_seed(spec)


def test_stop_exact_owned_vm_does_not_sudo_poll() -> None:
    ps_output = "1234 qemu-system-x86_64 -name ra-owned-vm -m 1024\n"
    calls = []

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((list(command), dict(kwargs)))
        return subprocess.CompletedProcess(command, 0, stdout=ps_output if command[0] == "ps" else "")

    with mock.patch.object(worker.subprocess, "run", side_effect=fake_run), mock.patch.object(
        worker,
        "process_active",
        return_value=False,
    ):
        worker.stop_exact_owned_vm("ra-owned-vm")

    sudo_calls = [row for row in calls if row[0][0] == "sudo"]
    assert len(sudo_calls) == 1
    assert sudo_calls[0][0] == ["sudo", "-n", "kill", "-TERM", "1234"]
    assert sudo_calls[0][1]["timeout"] == worker.SUDO_COMMAND_TIMEOUT_S


def test_formal_matrix_timeout_is_not_shortened() -> None:
    assert worker.SEED_REBUILD_TIMEOUT_S >= 4 * 60 * 60


def test_stale_cleanup_is_scoped_to_exact_task_and_current_uid() -> None:
    uid = os.getuid()
    ps_output = "\n".join(
        [
            f"1234 {uid} java -c /disk2/resource_adaptive_experiment_matrix_20260718/runtimes/a17_r1_tpcc_cpu/tpcc.xml",
            f"1235 {uid} java -c /disk2/resource_adaptive_experiment_matrix_20260718/runtimes/e17_r1_tpcc_none/tpcc.xml",
            f"1236 {uid + 1} java -c /disk2/resource_adaptive_experiment_matrix_20260718/runtimes/a17_r1_tpcc_cpu/tpcc.xml",
            f"1237 {uid} java -c /tmp/a17_r1_tpcc_cpu/tpcc.xml",
        ]
    )
    killed = []
    with mock.patch.object(
        worker.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 0, stdout=ps_output),
    ), mock.patch.object(worker.os, "kill", side_effect=lambda pid, sig: killed.append((pid, sig))), mock.patch.object(
        worker, "process_active", return_value=False
    ):
        assert worker.stop_stale_task_processes("a17_r1") == [1234]
    assert [pid for pid, _ in killed] == [1234]


def main() -> int:
    test_reusable_seed_requires_semantic_audit()
    test_stop_exact_owned_vm_does_not_sudo_poll()
    test_formal_matrix_timeout_is_not_shortened()
    test_stale_cleanup_is_scoped_to_exact_task_and_current_uid()
    print("double-loop VM task worker tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
