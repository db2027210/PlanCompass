#!/usr/bin/env python3
"""Fixture-backed offline E2E tests for the TPC-H systemd successor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def import_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = import_path(
    "tpch_systemd_compat_builder",
    HERE / "build_mysql_tpch_systemd_compat_successor_v1.py",
)
materializer = import_path(
    "tpch_systemd_oneshot_materializer",
    HERE / "mysql_tpch_systemd_oneshot_materializer_v1.py",
)


class SystemdCompatTests(unittest.TestCase):
    def setUp(self):
        self.fixture = builder.load(builder.HELP_FIXTURE)
        materializer.validate_help_fixture(self.fixture)
        self.attempt = (
            "tpch-v2-20260731t000000z-0123456789abcdef01234567"
        )
        self.spec = {
            "attempt_token": self.attempt,
            "runtime": {"target_root": "/runtime/python"},
            "bootstrap_execution": {
                "mode": "direct_host_systemd_transient_oneshot",
                "unit":
                    "ra-mysql-tpch-bootstrap-" + self.attempt + ".service",
                "service_type": "oneshot",
                "remain_after_exit": True,
                "ppid": 1,
                "token_transport": "token_file_or_in_process_only",
                "docker_ancestry_prohibited": True,
                "unit_retained_for_audit": True,
            },
        }

    def test_real_node04_help_fixture_matches_policy(self):
        text = self.fixture["stdout"]
        self.assertEqual(self.fixture["systemd_version"], 219)
        self.assertIn("--service-type=TYPE", text)
        self.assertIn("--remain-after-exit", text)
        self.assertIn("--collect", text)
        self.assertNotIn("--wait", text)

    def test_fixture_backed_materializer_e2e(self):
        plan = materializer.materialize(
            self.spec,
            bundle_path="/runtime/bundles/bundle",
            remote_spec="/authority/bootstrap_spec.json",
            remote_token_file="/authority/main_agent_token",
            remote_output="/authority/bootstrap_evidence.json",
            help_fixture=self.fixture,
        )
        argv = plan["argv"]
        self.assertIn("--service-type=oneshot", argv)
        self.assertIn("--remain-after-exit", argv)
        self.assertNotIn("--wait", argv)
        self.assertNotIn("--collect", argv)
        self.assertEqual(
            plan["controller_poll"]["success_state"],
            {
                "ActiveState": "active",
                "SubState": "exited",
                "ExecMainStatus": "0",
                "Result": "success",
            },
        )
        self.assertTrue(
            plan["controller_poll"]["unit_retained_for_audit"]
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "plan.json"
            materializer.atomic_new(output, plan)
            self.assertEqual(json.loads(output.read_text()), plan)

    def test_authority_binds_running_oneshot_identity(self):
        source = builder.authority_payload().decode("utf-8")
        compile(source, builder.AUTHORITY, "exec")
        for fragment in (
            '"mode": "direct_host_systemd_transient_oneshot"',
            '"service_type": "oneshot"',
            '"remain_after_exit": True',
            '"unit_retained_for_audit": True',
            '("activating", "start")',
            'int(properties.get("MainPID") or 0) != pid',
            'if os.getppid() != 1',
            '"process_starttime_ticks": _proc_starttime_ticks(pid)',
            '"namespaces": namespaces',
            '"control_group": control_group',
            '"docker_ancestry": False',
        ):
            self.assertIn(fragment, source)

    def test_00a0_and_old_material_are_zero_credit(self):
        builder.verify_inputs()
        invalid = builder.load(builder.INVALIDATION)
        self.assertEqual(invalid["bundle_id"], builder.PREDECESSOR_ID)
        self.assertEqual(invalid["accepted_rounds_credit"], 0)
        self.assertFalse(invalid["replay_or_rerun_authorized"])


if __name__ == "__main__":
    unittest.main()
