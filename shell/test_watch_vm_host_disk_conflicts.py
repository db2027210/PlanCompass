#!/usr/bin/env python3
"""Tests for target-scoped host disk guard completion."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.watch_vm_host_disk_conflicts import (
    TARGETS,
    all_converged,
    qemu_guest_name,
    stop_run_processes,
)


class TargetScopedConvergenceTest(unittest.TestCase):
    def test_qemu_guest_name_is_extracted_from_libvirt_command(self) -> None:
        cmdline = "/usr/bin/qemu-system-x86_64 -name guest=bonect-replay-v87-1,debug-threads=on"

        self.assertEqual("bonect-replay-v87-1", qemu_guest_name(cmdline))

    def test_io_only_requires_only_io_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            io_dir = root / "io"
            io_dir.mkdir()
            (io_dir / "final_for_io.json").write_text(
                json.dumps({"converged": True, "stop_reason": "epsilon_converged"}),
                encoding="utf-8",
            )

            self.assertTrue(all_converged(root, ("io",)))
            self.assertFalse(all_converged(root, TARGETS))

    def test_wrong_stop_reason_is_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            io_dir = root / "io"
            io_dir.mkdir()
            (io_dir / "final_for_io.json").write_text(
                json.dumps({"converged": True, "stop_reason": "max_rounds"}),
                encoding="utf-8",
            )

            self.assertFalse(all_converged(root, ("io",)))

    def test_explicit_round_limit_can_be_terminal_without_claiming_convergence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            io_dir = root / "io"
            io_dir.mkdir()
            (io_dir / "final_for_io.json").write_text(
                json.dumps({"converged": False, "stop_reason": "max_rounds"}),
                encoding="utf-8",
            )

            self.assertFalse(all_converged(root, ("io",)))
            self.assertTrue(all_converged(root, ("io",), ("max_rounds",)))

    def test_invalidated_run_cleanup_kills_process_group_and_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_root = Path(temp_dir).resolve()
            code = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen(['sleep','60']); "
                "print(child.pid, flush=True); "
                "time.sleep(60)"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", code, str(run_root)],
                stdout=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            assert proc.stdout is not None
            child_pid = int(proc.stdout.readline().strip())
            try:
                stop_run_processes(run_root, f"missing-test-session-{os.getpid()}")
                proc.wait(timeout=3.0)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    stat_path = Path(f"/proc/{child_pid}/stat")
                    if not stat_path.exists():
                        break
                    state = stat_path.read_text(encoding="utf-8").split()[2]
                    if state == "Z":
                        break
                    time.sleep(0.05)
                else:
                    self.fail(f"descendant process survived run cleanup: pid={child_pid}")
            finally:
                proc.stdout.close()
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    unittest.main()
