from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from isolated_mysql_instances import build_plan, execute_plan, load_config, preflight, remove_owned


def completed(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


class FakeRunner:
    def __init__(self, inventory=""):
        self.inventory = inventory
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        if argv[:3] == ["docker", "ps", "-a"]:
            return completed(argv, out=self.inventory)
        return completed(argv)


class IsolatedInstancesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "label_key": "resource-adaptive-mysql.owner", "label_value": "test-owner",
            "name_prefix": "ra-test-", "target_root": "/disk1", "image": "mysql:8.0",
            "external_experiment_markers": ["hybench", "experiment"],
            "instances": [{"id": "one", "port": 14307, "cpuset": "8-9", "memory": "2g",
                           "blkio_weight": 300, "pids_limit": 128,
                           "datadir": "/disk1/ra_test_nonexistent/one"}],
        }

    def test_default_config_and_plan_have_all_isolation_controls(self):
        cfg = load_config()
        plan = build_plan(cfg)
        self.assertEqual(plan["mode"], "dry_run")
        command = plan["container_commands"][0]
        for option in ("--label", "--cpuset-cpus", "--memory", "--blkio-weight", "--pids-limit", "--publish", "--mount"):
            self.assertIn(option, command)
        self.assertEqual(plan["clone_operations"], [])

    def test_preflight_detects_foreign_name_port_and_external_experiment(self):
        rows = [
            {"Names": "ra-test-one", "Labels": "someone=else", "State": "exited", "Status": "Exited"},
            {"Names": "hybench-live", "Labels": "suite=experiment", "State": "running", "Status": "Up 1h"},
        ]
        fake = FakeRunner("\n".join(json.dumps(row) for row in rows))
        audit = preflight(self.cfg, fake, port_probe=lambda port: False)
        self.assertFalse(audit["ok"])
        self.assertTrue(any("non-owned" in error for error in audit["errors"]))
        self.assertTrue(any("port is occupied" in error for error in audit["errors"]))
        self.assertEqual(audit["external_active"], ["hybench-live"])
        self.assertEqual(len(fake.calls), 1)

    def test_owned_existing_resource_is_not_treated_as_foreign(self):
        row = {"Names": "ra-test-one", "Labels": "resource-adaptive-mysql.owner=test-owner",
               "State": "running", "Status": "Up"}
        audit = preflight(self.cfg, FakeRunner(json.dumps(row)), port_probe=lambda port: False)
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["owned_active"], ["ra-test-one"])

    def test_clone_needs_second_opt_in_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as source_dir:
            source = Path(source_dir)
            plan = build_plan(self.cfg, source)
            fake = FakeRunner()
            with self.assertRaisesRegex(RuntimeError, "allow-clone"):
                execute_plan(self.cfg, plan, allow_clone=False, source_datadir=source, runner=fake)
            self.assertEqual(fake.calls, [])
            self.assertEqual(plan["clone_operations"][0]["source_mode"], "read_only")

    def test_remove_refuses_foreign_label_without_rm_call(self):
        class InspectRunner(FakeRunner):
            def __call__(self, argv):
                self.calls.append(list(argv))
                return completed(argv, out=json.dumps({"resource-adaptive-mysql.owner": "foreign"}))
        fake = InspectRunner()
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            remove_owned("ra-test-one", self.cfg, fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][0:2], ["docker", "inspect"])

    def test_remove_owned_uses_exact_label_then_rm(self):
        class InspectRunner(FakeRunner):
            def __call__(self, argv):
                self.calls.append(list(argv))
                if argv[1] == "inspect":
                    return completed(argv, out=json.dumps({"resource-adaptive-mysql.owner": "test-owner"}))
                return completed(argv)
        fake = InspectRunner()
        remove_owned("ra-test-one", self.cfg, fake)
        self.assertEqual(fake.calls[-1], ["docker", "rm", "-f", "ra-test-one"])


if __name__ == "__main__":
    unittest.main()
