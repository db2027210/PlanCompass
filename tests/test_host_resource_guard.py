"""Tests for scripts/host_resource_guard.py — real sample→decide→act path."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "host_resource_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("host_resource_guard", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["host_resource_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


def _snap(**overrides) -> "guard.MetricSnapshot":
    base = dict(
        ts=1.0,
        cpu_busy_pct=10.0,
        mem_used_pct=20.0,
        mem_available_bytes=100 * 1024**3,
        mem_total_bytes=1024 * 1024**3,
        iowait_pct=1.0,
        disk_util_avg_pct=5.0,
        disk_used_pct={"/disk1": 50.0, "/disk2": 50.0, "/disk3": 50.0},
        notes=[],
    )
    base.update(overrides)
    return guard.MetricSnapshot(**base)


class EvaluateBreachTests(unittest.TestCase):
    def setUp(self):
        self.th = guard.Thresholds()

    def test_all_clear(self):
        reasons = guard.evaluate_breach(_snap(), self.th)
        self.assertEqual(reasons, [])

    def test_cpu_only(self):
        reasons = guard.evaluate_breach(_snap(cpu_busy_pct=95.0), self.th)
        self.assertEqual(reasons, ["cpu_saturated"])

    def test_cpu_below_threshold_no_breach(self):
        reasons = guard.evaluate_breach(_snap(cpu_busy_pct=94.9), self.th)
        self.assertEqual(reasons, [])

    def test_memory_used_pct(self):
        reasons = guard.evaluate_breach(_snap(mem_used_pct=95.0), self.th)
        self.assertEqual(reasons, ["memory_over_limit"])

    def test_memory_available_floor(self):
        # Available below 8 GiB floor on a large host.
        reasons = guard.evaluate_breach(
            _snap(
                mem_used_pct=50.0,
                mem_available_bytes=4 * 1024**3,
                mem_total_bytes=1024 * 1024**3,
            ),
            self.th,
        )
        self.assertEqual(reasons, ["memory_over_limit"])

    def test_io_iowait(self):
        reasons = guard.evaluate_breach(_snap(iowait_pct=40.0), self.th)
        self.assertEqual(reasons, ["io_blocked"])

    def test_io_disk_util(self):
        reasons = guard.evaluate_breach(_snap(disk_util_avg_pct=90.0), self.th)
        self.assertEqual(reasons, ["io_blocked"])

    def test_disk1_over_90(self):
        reasons = guard.evaluate_breach(
            _snap(disk_used_pct={"/disk1": 90.1, "/disk2": 10.0, "/disk3": 10.0}),
            self.th,
        )
        self.assertEqual(reasons, ["disk_over_limit:/disk1"])

    def test_disk2_over_90(self):
        reasons = guard.evaluate_breach(
            _snap(disk_used_pct={"/disk1": 10.0, "/disk2": 91.0, "/disk3": 10.0}),
            self.th,
        )
        self.assertEqual(reasons, ["disk_over_limit:/disk2"])

    def test_disk3_over_90(self):
        reasons = guard.evaluate_breach(
            _snap(disk_used_pct={"/disk1": 10.0, "/disk2": 10.0, "/disk3": 90.0001}),
            self.th,
        )
        self.assertEqual(reasons, ["disk_over_limit:/disk3"])

    def test_disk_exactly_90_is_not_breach(self):

        reasons = guard.evaluate_breach(
            _snap(disk_used_pct={"/disk1": 90.0, "/disk2": 90.0, "/disk3": 90.0}),
            self.th,
        )
        self.assertEqual(reasons, [])

    def test_combined_breach(self):
        reasons = guard.evaluate_breach(
            _snap(
                cpu_busy_pct=99.0,
                mem_used_pct=96.0,
                iowait_pct=50.0,
                disk_used_pct={"/disk1": 91.0, "/disk2": 50.0, "/disk3": 92.0},
            ),
            self.th,
        )
        self.assertEqual(
            reasons,
            [
                "cpu_saturated",
                "memory_over_limit",
                "io_blocked",
                "disk_over_limit:/disk1",
                "disk_over_limit:/disk3",
            ],
        )


class SelectKillTargetsTests(unittest.TestCase):
    def test_selects_only_anonymous_experiment_vm_docker(self):
        monitor_pid = 111
        candidates = [
            guard.KillTarget("docker", "c1", "ra_mysql_gd_worker", "anonymous"),
            guard.KillTarget("docker", "c2", "baibh-overleaf-pro", "anonymous"),  # filtered earlier by discover; if present keep only if owner match — still anonymous owner so would pass select; name filter is discovery
            guard.KillTarget("qemu", "222", "ra-rootless-vm41", "anonymous"),
            guard.KillTarget("qemu", "333", "other-user-vm", "alice"),
            guard.KillTarget("process", "444", "run_mysql_gd_matrix", "anonymous"),
            guard.KillTarget("process", "555", "sshd", "anonymous"),
            guard.KillTarget("process", str(monitor_pid), "host_resource_guard", "anonymous"),
            guard.KillTarget("process", "666", "foreign", "root"),
        ]
        # select_kill_targets only filters owner/kind/self — not experiment-ness
        # (discovery already scoped). So docker c2 would pass if handed in.
        selected = guard.select_kill_targets(
            candidates, owner="anonymous", monitor_pid=monitor_pid
        )
        keys = {(t.kind, t.identity) for t in selected}
        self.assertIn(("docker", "c1"), keys)
        self.assertIn(("docker", "c2"), keys)  # select trusts discovery filter
        self.assertIn(("qemu", "222"), keys)
        self.assertIn(("process", "444"), keys)
        self.assertNotIn(("qemu", "333"), keys)  # wrong owner
        self.assertNotIn(("process", "666"), keys)
        self.assertNotIn(("process", str(monitor_pid)), keys)

    def test_excludes_monitor_pid_even_if_listed_as_qemu(self):
        monitor_pid = 9999
        selected = guard.select_kill_targets(
            [guard.KillTarget("qemu", str(monitor_pid), "self", "anonymous")],
            owner="anonymous",
            monitor_pid=monitor_pid,
        )
        self.assertEqual(selected, [])


class DecideAndActPathTests(unittest.TestCase):
    """Drive shipped decide_and_act with injected metrics/targets — no oracle reimpl."""

    def setUp(self):
        self.th = guard.Thresholds()
        self.acted: List[guard.KillTarget] = []

        def act_fn(target: guard.KillTarget, dry_run: bool) -> guard.ActResult:
            self.acted.append(target)
            return guard.ActResult(
                target=target,
                action="would_stop" if dry_run else "stop",
                detail="test",
                dry_run=dry_run,
            )

        self.act_fn = act_fn
        self.candidates = [
            guard.KillTarget("docker", "d1", "ra_mysql_gd_hybench", "anonymous"),
            guard.KillTarget("qemu", "2001", "ra-rootless-vm1", "anonymous"),
            guard.KillTarget("process", "3001", "run_mysql_gd_matrix", "anonymous"),
            guard.KillTarget("process", "4001", "should-not", "root"),
            guard.KillTarget("process", "42", "monitor-self", "anonymous"),
            guard.KillTarget("docker", "foreign", "baibh-overleaf", "alice"),
        ]
        self.monitor_pid = 42

    def test_all_clear_does_not_act(self):
        reasons, targets, results = guard.decide_and_act(
            _snap(),
            self.th,
            self.candidates,
            owner="anonymous",
            monitor_pid=self.monitor_pid,
            dry_run=True,
            act_fn=self.act_fn,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(self.acted, [])
        self.assertEqual(results, [])
        # Targets still computed for logging even on all-clear.
        ids = {(t.kind, t.identity) for t in targets}
        self.assertEqual(
            ids,
            {("docker", "d1"), ("qemu", "2001"), ("process", "3001")},
        )

    def test_cpu_breach_invokes_act_only_on_anonymous_targets_dry_run(self):
        reasons, targets, results = guard.decide_and_act(
            _snap(cpu_busy_pct=98.0),
            self.th,
            self.candidates,
            owner="anonymous",
            monitor_pid=self.monitor_pid,
            dry_run=True,
            act_fn=self.act_fn,
        )
        self.assertEqual(reasons, ["cpu_saturated"])
        self.assertEqual(len(self.acted), 3)
        self.assertEqual({t.identity for t in self.acted}, {"d1", "2001", "3001"})
        self.assertTrue(all(r.action == "would_stop" and r.dry_run for r in results))
        self.assertEqual(len(results), 3)

    def test_memory_breach_acts(self):
        reasons, _, results = guard.decide_and_act(
            _snap(mem_used_pct=99.0),
            self.th,
            self.candidates,
            owner="anonymous",
            monitor_pid=self.monitor_pid,
            dry_run=True,
            act_fn=self.act_fn,
        )
        self.assertEqual(reasons, ["memory_over_limit"])
        self.assertEqual(len(results), 3)

    def test_io_breach_acts(self):
        reasons, _, results = guard.decide_and_act(
            _snap(iowait_pct=55.0),
            self.th,
            self.candidates,
            owner="anonymous",
            monitor_pid=self.monitor_pid,
            dry_run=True,
            act_fn=self.act_fn,
        )
        self.assertEqual(reasons, ["io_blocked"])
        self.assertEqual(len(results), 3)

    def test_each_disk_breach_acts(self):
        for path in ("/disk1", "/disk2", "/disk3"):
            self.acted.clear()
            used = {p: 50.0 for p in ("/disk1", "/disk2", "/disk3")}
            used[path] = 90.5
            reasons, _, results = guard.decide_and_act(
                _snap(disk_used_pct=used),
                self.th,
                self.candidates,
                owner="anonymous",
                monitor_pid=self.monitor_pid,
                dry_run=True,
                act_fn=self.act_fn,
            )
            self.assertEqual(reasons, [f"disk_over_limit:{path}"], path)
            self.assertEqual(len(results), 3, path)

    def test_live_act_flag_passed_through(self):
        _, _, results = guard.decide_and_act(
            _snap(cpu_busy_pct=100.0),
            self.th,
            self.candidates,
            owner="anonymous",
            monitor_pid=self.monitor_pid,
            dry_run=False,
            act_fn=self.act_fn,
        )
        self.assertTrue(all(r.action == "stop" and not r.dry_run for r in results))


class DefaultActDryRunTests(unittest.TestCase):
    def test_default_act_dry_run_never_signals(self):
        calls = []

        def fake_kill(pid, sig):
            calls.append((pid, sig))

        target = guard.KillTarget("process", "12345", "x", "anonymous")
        result = guard.default_act(
            target, dry_run=True, signal_fn=fake_kill, docker_stop_fn=lambda i, t: (True, "x")
        )
        self.assertEqual(result.action, "would_stop")
        self.assertEqual(calls, [])

    def test_default_act_live_process_sigterm(self):
        calls = []

        def fake_kill(pid, sig):
            calls.append((pid, sig))
            # Pretend process dies after SIGTERM so no SIGKILL.
            raise ProcessLookupError()

        target = guard.KillTarget("process", "12345", "x", "anonymous")
        result = guard.default_act(
            target, dry_run=False, signal_fn=fake_kill, stop_timeout_s=0.2
        )
        # First SIGTERM raises ProcessLookupError → "already gone"
        self.assertEqual(result.action, "stop")
        self.assertEqual(calls, [(12345, 15)])  # SIGTERM == 15

    def test_default_act_live_docker(self):
        stopped = []

        def fake_stop(identity, timeout):
            stopped.append((identity, timeout))
            return True, "ok"

        target = guard.KillTarget("docker", "abc", "ra_mysql_x", "anonymous")
        result = guard.default_act(
            target, dry_run=False, docker_stop_fn=fake_stop, stop_timeout_s=2.0
        )
        self.assertEqual(result.action, "stop")
        self.assertEqual(stopped, [("abc", 2.0)])


class DockerNameFilterTests(unittest.TestCase):
    def test_discover_docker_filters_foreign_keeps_experiment(self):
        rows = [
            {"ID": "1", "Names": "ra_mysql_gd_hybench_xp_auto6", "Image": "mysql:8.0"},
            {"ID": "2", "Names": "ra-mysql-d1-job-w2", "Image": "mysql:8.0"},
            {"ID": "3", "Names": "baibh-overleaf-pro", "Image": "overleaf"},
            {"ID": "4", "Names": "redis", "Image": "redis:7.4"},
            {"ID": "5", "Names": "zzk-opengauss", "Image": "opengauss"},
            {"ID": "6", "Names": "gptuner_mysql_benchbase", "Image": "mysql:8.0"},
        ]
        found = guard.discover_docker_targets(
            owner="anonymous", docker_ps_fn=lambda: rows
        )
        names = {t.name for t in found}
        self.assertEqual(
            names,
            {
                "ra_mysql_gd_hybench_xp_auto6",
                "ra-mysql-d1-job-w2",
                "gptuner_mysql_benchbase",
            },
        )
        self.assertTrue(all(t.kind == "docker" and t.owner == "anonymous" for t in found))


class RunOnceIntegrationTests(unittest.TestCase):
    def test_run_once_all_clear_and_breach_with_injected_sample(self):
        acted = []

        def act_fn(t, dry):
            acted.append((t.identity, dry))
            return guard.ActResult(t, "would_stop", dry_run=dry)

        th = guard.Thresholds()
        # all-clear
        rc = guard.run_once(
            thresholds=th,
            owner="anonymous",
            monitor_pid=1,
            dry_run=True,
            sample_fn=lambda: _snap(),
            discover_fn=lambda: [
                guard.KillTarget("docker", "d1", "ra_mysql", "anonymous"),
            ],
            act_fn=act_fn,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(acted, [])

        # breach
        rc = guard.run_once(
            thresholds=th,
            owner="anonymous",
            monitor_pid=1,
            dry_run=True,
            sample_fn=lambda: _snap(cpu_busy_pct=99.0),
            discover_fn=lambda: [
                guard.KillTarget("docker", "d1", "ra_mysql", "anonymous"),
                guard.KillTarget("process", "1", "self", "anonymous"),
            ],
            act_fn=act_fn,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(acted, [("d1", True)])

    def test_force_breach_uses_real_evaluate_path(self):
        acted = []

        def act_fn(t, dry):
            acted.append(t.identity)
            return guard.ActResult(t, "would_stop", dry_run=True)

        # Inject calm metrics; force_breach should still trip via decide path.
        rc = guard.run_once(
            thresholds=guard.Thresholds(),
            owner="anonymous",
            monitor_pid=1,
            dry_run=True,
            force_breach=True,
            sample_fn=lambda: _snap(cpu_busy_pct=1.0),
            discover_fn=lambda: [
                guard.KillTarget("qemu", "9", "vm", "anonymous"),
            ],
            act_fn=act_fn,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(acted, ["9"])


class ExperimentCmdlineHeuristicTests(unittest.TestCase):
    def test_markers_match(self):
        self.assertTrue(
            guard._cmdline_looks_like_experiment(
                "python run_mysql_gd_matrix.py --foo", "python"
            )
        )
        self.assertTrue(
            guard._cmdline_looks_like_experiment(
                "qemu-system-x86_64 -name ra", "qemu-system-x86"
            )
            or guard._is_qemu("qemu-system-x86_64", "qemu-system-x86_64 -name ra")
        )
        self.assertFalse(
            guard._cmdline_looks_like_experiment(
                "python scripts/host_resource_guard.py --once", "python"
            )
        )


class RealSampleSmokeTests(unittest.TestCase):
    """Exercise real host sampling once (no kill)."""

    def test_sample_metrics_has_required_fields(self):
        snap = guard.sample_metrics(
            disk_paths=("/disk1", "/disk2", "/disk3"),
            cpu_sample_s=0.15,
            io_sample_s=0.15,
        )
        self.assertGreaterEqual(snap.cpu_busy_pct, 0.0)
        self.assertLessEqual(snap.cpu_busy_pct, 100.5)
        self.assertGreater(snap.mem_total_bytes, 0)
        self.assertGreaterEqual(snap.mem_available_bytes, 0)
        for path in ("/disk1", "/disk2", "/disk3"):
            self.assertIn(path, snap.disk_used_pct)
            self.assertGreaterEqual(snap.disk_used_pct[path], 0.0)
            self.assertLessEqual(snap.disk_used_pct[path], 100.0)

    def test_main_once_dry_run_exits_zero(self):
        rc = guard.main(["--once", "--interval", "1"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
