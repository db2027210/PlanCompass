#!/usr/bin/env python3
"""Fixture-backed offline E2E tests for exact live TPC-H daemon adoption."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent


def import_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("module is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = import_path(
    "tpch_live_daemon_builder",
    HERE / "build_mysql_tpch_live_daemon_successor_v1.py",
)


class AuthorityError(RuntimeError):
    pass


def authority_process_helper():
    tree = ast.parse(builder.authority_payload().decode("utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_live_daemon_process_readback"
    )
    namespace = {
        "Any": object,
        "Mapping": dict,
        "Path": Path,
        "os": os,
        "hashlib": hashlib,
        "AuthorityError": AuthorityError,
        "_proc_starttime_ticks": lambda pid: 233390522,
    }
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[function], type_ignores=[])
            ),
            "_live_daemon_process_readback",
            "exec",
        ),
        namespace,
    )
    return namespace["_live_daemon_process_readback"]


class LiveDaemonTests(unittest.TestCase):
    def test_real_systemctl_show_fixture_exactly_matches_contract(self):
        builder.validate_fixtures()
        fixture = builder.load(builder.SYSTEMCTL_FIXTURE)
        contract = builder.load(builder.CONTRACT)
        properties = builder.parse_properties(fixture["stdout"])
        self.assertEqual(properties, {
            **fixture["exact_projection"],
            "ExecStart": properties["ExecStart"],
        })
        self.assertIn(
            "argv[]=" + " ".join(contract["exec_argv"]) + " ;",
            properties["ExecStart"],
        )
        self.assertEqual(properties["FragmentPath"], contract["unit_fragment"])
        self.assertEqual(properties["Transient"], "yes")
        self.assertEqual(int(properties["CPUShares"]), 256)
        self.assertEqual(int(properties["MemoryLimit"]), 2147483648)
        self.assertEqual(int(properties["TasksMax"]), 512)

    def test_real_proc_fixture_passes_sealed_process_validator(self):
        helper = authority_process_helper()
        contract = builder.load(builder.CONTRACT)
        cmdline = (
            b"\0".join(item.encode() for item in contract["main_pid_argv"])
            + b"\0"
        )
        self.assertEqual(
            hashlib.sha256(cmdline).hexdigest(),
            contract["main_pid_cmdline_sha256"],
        )

        def read_text(path, encoding=None):
            raw = str(path)
            if raw.endswith("/status"):
                return "PPid:\t1\nCpus_allowed_list:\t34-35,70-71\n"
            if raw.endswith("/cgroup"):
                return "\n".join(contract["cgroup_v1_lines"]) + "\n"
            raise AssertionError(raw)

        def read_bytes(path):
            if str(path).endswith("/cmdline"):
                return cmdline
            raise AssertionError(path)

        def readlink(path):
            name = path.rsplit("/", 1)[-1]
            return contract["mandatory_namespace_identities"][name]

        with (
            mock.patch.object(Path, "read_text", read_text),
            mock.patch.object(Path, "read_bytes", read_bytes),
            mock.patch.object(os, "readlink", side_effect=readlink),
            mock.patch.object(os.path, "lexists", return_value=False),
        ):
            value = helper(97636, contract)
        self.assertEqual(value["ppid"], 1)
        self.assertEqual(value["cpus_allowed_list"], "34-35,70-71")
        self.assertEqual(value["starttime_ticks"], 233390522)
        self.assertFalse(
            value["optional_cgroup_namespace"]["supported"]
        )

    def test_changed_fragment_or_resource_property_is_rejected_in_source(self):
        source = builder.authority_payload().decode("utf-8")
        compile(source, builder.AUTHORITY, "exec")
        collector = builder.collector_payload().decode("utf-8")
        compile(collector, builder.COLLECTOR, "exec")
        for fragment in (
            'properties.get("FragmentPath") != contract["unit_fragment"]',
            'properties.get("Transient") != "yes"',
            'int(properties.get("CPUShares") or 0) != contract["cpu_shares"]',
            '!= contract["memory_limit_bytes"]',
            'int(properties.get("TasksMax") or 0) != contract["tasks_max"]',
            'properties.get("ControlGroup") != contract["control_group"]',
            'main_pid != contract["main_pid"]',
            'status.get("Cpus_allowed_list")',
            'argv != contract["main_pid_argv"]',
        ):
            self.assertIn(fragment, source)
            self.assertIn(fragment, collector)

    def test_static_unit_is_removed_and_deployment_is_prohibited(self):
        predecessor = builder.verify_predecessor()
        payloads = builder.payloads(predecessor)
        self.assertNotIn(builder.STATIC_UNIT_RELATIVE, payloads)
        runtime = builder.load(builder.PREDECESSOR / builder.RUNTIME)
        successor_runtime = __import__("json").loads(builder.runtime_payload())
        self.assertNotEqual(
            runtime["dedicated_daemon"],
            successor_runtime["dedicated_daemon"],
        )
        self.assertTrue(
            successor_runtime["dedicated_daemon_deployment_policy"][
                "static_unit_deployment_prohibited"
            ]
        )

    def test_9aa_attempt_is_zero_credit_and_no_rerun(self):
        builder.verify_inputs()
        invalid = builder.load(builder.INVALIDATION)
        self.assertFalse(invalid["container_started"])
        self.assertFalse(invalid["database_started"])
        self.assertFalse(invalid["workload_started"])
        self.assertEqual(invalid["accepted_rounds_credit"], 0)
        self.assertFalse(invalid["replay_or_rerun_authorized"])


if __name__ == "__main__":
    unittest.main()
