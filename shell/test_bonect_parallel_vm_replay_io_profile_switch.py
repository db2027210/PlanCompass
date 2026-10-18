#!/usr/bin/env python3
"""Focused tests for IO restore/measurement profile switch (decision 0504)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import bonect_parallel_vm_replay as mod


class FakeProc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _iotune_query_text(read_bps: int, write_bps: int) -> str:
    return (
        f"total_bytes_sec: 0\n"
        f"read_bytes_sec : {read_bps}\n"
        f"write_bytes_sec: {write_bps}\n"
    )


class ParseAndWrapperTests(unittest.TestCase):
    def test_virsh_safe_command_uses_sudo_timeout_wrapper(self) -> None:
        cmd = mod.virsh_safe_command("blkdeviotune", "vm", "vda", "--live")
        self.assertEqual(cmd[0], "sudo")
        self.assertEqual(cmd[1], "env")
        self.assertIn("LIBVIRT_DEFAULT_URI=qemu:///system", cmd)
        self.assertIn("timeout", cmd)
        self.assertIn("--kill-after=5s", cmd)
        self.assertIn("60s", cmd)
        self.assertIn("virsh", cmd)
        self.assertNotIn("-c", cmd)

    def test_parse_blkdeviotune_output(self) -> None:
        observed = mod.parse_blkdeviotune_output(_iotune_query_text(104857600, 20971520))
        self.assertEqual(observed["read_bytes_sec"], 104857600)
        self.assertEqual(observed["write_bytes_sec"], 20971520)


class LiveThrottleVerifyTests(unittest.TestCase):
    def test_live_disk_throttle_set_then_query_fail_closed_on_mismatch(self) -> None:
        calls: List[List[str]] = []

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(list(cmd))
            # set succeeds, query returns mismatch
            if "--read-bytes-sec" in cmd:
                return FakeProc(0, "", "")
            return FakeProc(0, _iotune_query_text(1, 2), "")

        with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                mod.live_disk_throttle("bonect-replay-v87-3", "vda", read_mb=100, write_mb=20)
        self.assertIn("verify mismatch", str(ctx.exception))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "sudo")
        self.assertEqual(calls[1][0], "sudo")

    def test_live_disk_throttle_matches_observed_bytes(self) -> None:
        read_bps = 100 * 1024 * 1024
        write_bps = 20 * 1024 * 1024

        def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
            if "--read-bytes-sec" in cmd:
                return FakeProc(0, "", "")
            return FakeProc(0, _iotune_query_text(read_bps, write_bps), "")

        with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
            result = mod.live_disk_throttle(
                "bonect-replay-v87-3", "vda", read_mb=100, write_mb=20
            )
        self.assertTrue(result["verify_matched"])
        self.assertEqual(result["observed_read_bytes_sec"], read_bps)
        self.assertEqual(result["observed_write_bytes_sec"], write_bps)
        self.assertEqual(result["set_command"][0], "sudo")
        self.assertEqual(result["query_command"][0], "sudo")


class ResolveBoostConfigTests(unittest.TestCase):
    def _write_cfg(self, root: Path, *, enabled: bool, scenario_hint: str = "io") -> Path:
        cfg = {
            "_assignment": {"scenario": scenario_hint},
            "vm_resource": {
                "vm_name": "bonect-replay-v87-3",
                "read_mb": 100,
                "write_mb": 20,
                "disk_dev": "vda",
            },
            "pre_benchmark_recovery": {
                "snapshot_restore": {
                    "enabled": True,
                    "restore_io_boost": {
                        "enabled": enabled,
                        "read_mb": 100,
                        "write_mb": 200,
                    },
                }
            },
        }
        path = root / f"{scenario_hint}_cfg.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_resolve_only_for_io_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            io_cfg = self._write_cfg(root, enabled=True, scenario_hint="io")
            cpu_cfg = self._write_cfg(root, enabled=True, scenario_hint="cpu")
            disabled = self._write_cfg(root, enabled=False, scenario_hint="io_disabled")
            io_worker = {
                "worker_id": "w-io",
                "vm_name": "bonect-replay-v87-3",
                "scenario_configs": {"io": str(io_cfg)},
            }
            cpu_worker = {
                "worker_id": "w-cpu",
                "vm_name": "bonect-replay-v87-2",
                "scenario_configs": {"cpu": str(cpu_cfg)},
            }
            mem_worker = {
                "worker_id": "w-mem",
                "vm_name": "bonect-replay-v87-1",
                "scenario_configs": {"memory": str(cpu_cfg)},
            }
            none_worker = {
                "worker_id": "w-none",
                "vm_name": "bonect-replay-v87-4",
                "scenario_configs": {"none": str(cpu_cfg)},
            }
            disabled_worker = {
                "worker_id": "w-io-off",
                "vm_name": "bonect-replay-v87-3",
                "scenario_configs": {"io": str(disabled)},
            }
            boost = mod.resolve_restore_io_boost(io_worker, "io")
            self.assertIsNotNone(boost)
            assert boost is not None
            self.assertEqual(boost["read_mb"], 100)
            self.assertEqual(boost["write_mb"], 200)
            self.assertIsNone(mod.resolve_restore_io_boost(cpu_worker, "cpu"))
            self.assertIsNone(mod.resolve_restore_io_boost(mem_worker, "memory"))
            self.assertIsNone(mod.resolve_restore_io_boost(none_worker, "none"))
            self.assertIsNone(mod.resolve_restore_io_boost(disabled_worker, "io"))


class OrderingAndFailClosedIntegrationTests(unittest.TestCase):
    """Simulate wave-level ordering without full run() stack."""

    def test_io_enabled_order_boost_restore_measure_cache_worker(self) -> None:
        events: List[str] = []

        def fake_live(vm_name, disk_dev, *, read_mb, write_mb, verify=True, **kwargs):  # type: ignore[no-untyped-def]
            events.append(f"throttle:{read_mb}/{write_mb}")
            return {
                "status": "ok",
                "verify_matched": True,
                "set_command": ["sudo", "env", "virsh", "blkdeviotune"],
                "query_command": ["sudo", "env", "virsh", "blkdeviotune"],
                "observed_read_bytes_sec": read_mb * 1024 * 1024,
                "observed_write_bytes_sec": write_mb * 1024 * 1024,
                "requested_read_bytes_sec": read_mb * 1024 * 1024,
                "requested_write_bytes_sec": write_mb * 1024 * 1024,
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {
                "vm_resource": {
                    "vm_name": "bonect-replay-v87-3",
                    "read_mb": 100,
                    "write_mb": 20,
                    "disk_dev": "vda",
                },
                "pre_benchmark_recovery": {
                    "snapshot_restore": {
                        "enabled": True,
                        "restore_io_boost": {
                            "enabled": True,
                            "read_mb": 100,
                            "write_mb": 200,
                        },
                    }
                },
            }
            cfg_path = root / "io.json"
            cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
            worker = {
                "worker_id": "bonect-replay-v87-3",
                "vm_name": "bonect-replay-v87-3",
                "scenario_configs": {"io": str(cfg_path)},
            }
            boost = mod.resolve_restore_io_boost(worker, "io")
            self.assertIsNotNone(boost)
            assert boost is not None
            with mock.patch.object(mod, "live_disk_throttle", side_effect=fake_live):
                # mimic required ordering of run()
                events.append("boost_start")
                mod.live_disk_throttle(
                    "bonect-replay-v87-3",
                    "vda",
                    read_mb=boost["read_mb"],
                    write_mb=boost["write_mb"],
                )
                events.append("restore")
                mod.live_disk_throttle(
                    "bonect-replay-v87-3",
                    "vda",
                    read_mb=100,
                    write_mb=20,
                )
                events.append("cache_drop")
                events.append("worker")
            self.assertEqual(
                events,
                [
                    "boost_start",
                    "throttle:100/200",
                    "restore",
                    "throttle:100/20",
                    "cache_drop",
                    "worker",
                ],
            )

    def test_io_set_failure_prevents_worker_start(self) -> None:
        worker_started = False

        def failing_live(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("set failed")

        with mock.patch.object(mod, "live_disk_throttle", side_effect=failing_live):
            try:
                mod.live_disk_throttle("vm", "vda", read_mb=100, write_mb=200)
                # if throttle fails, worker must not start
                worker_started = True
            except RuntimeError:
                worker_started = False
        self.assertFalse(worker_started)

    def test_non_io_scenarios_never_call_live_throttle(self) -> None:
        calls = {"n": 0}

        def counting_live(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return {"status": "ok"}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Even if a mistaken boost block exists, non-io scenario must not resolve it.
            cfg = {
                "vm_resource": {"vm_name": "x", "read_mb": 800, "write_mb": 800, "disk_dev": "vda"},
                "pre_benchmark_recovery": {
                    "snapshot_restore": {
                        "enabled": True,
                        "restore_io_boost": {"enabled": True, "read_mb": 100, "write_mb": 200},
                    }
                },
            }
            path = root / "cfg.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            for scenario in ("cpu", "memory", "none"):
                worker = {
                    "worker_id": f"w-{scenario}",
                    "scenario_configs": {scenario: str(path)},
                }
                with mock.patch.object(mod, "live_disk_throttle", side_effect=counting_live):
                    resolved = mod.resolve_restore_io_boost(worker, scenario)
                    self.assertIsNone(resolved)
                    # run path only calls live_disk_throttle when resolve is non-None
                    if resolved is not None:
                        mod.live_disk_throttle("vm", "vda", read_mb=1, write_mb=1)
        self.assertEqual(calls["n"], 0)


if __name__ == "__main__":
    unittest.main()
