#!/usr/bin/env python3
"""Offline E2E tests for node04 namespace capability compatibility."""

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
    "tpch_namespace_compat_builder",
    HERE / "build_mysql_tpch_namespace_compat_successor_v1.py",
)


class AuthorityError(RuntimeError):
    pass


def sealed_namespace_helper():
    tree = ast.parse(builder.authority_payload().decode("utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_namespace_identity_readback"
    )
    namespace = {
        "Any": object,
        "os": os,
        "AuthorityError": AuthorityError,
    }
    exec(
        compile(
            ast.fix_missing_locations(
                ast.Module(body=[function], type_ignores=[])
            ),
            "_namespace_identity_readback",
            "exec",
        ),
        namespace,
    )
    return namespace["_namespace_identity_readback"]


class NamespaceCompatTests(unittest.TestCase):
    def test_real_node04_fixture_is_exact(self):
        fixture = builder.load(builder.NAMESPACE_FIXTURE)
        stdout = fixture["stdout"]
        self.assertEqual(
            hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
            "544486c6bdaab05ad05efa950b6afa40eb11b98d3dbfa4f7fa7aa795cd63c215",
        )
        self.assertEqual(
            fixture["mandatory_namespaces"],
            ["ipc", "mnt", "net", "pid", "user", "uts"],
        )
        self.assertEqual(
            fixture["optional_namespaces"]["cgroup"],
            {"supported": False, "path_absent": True},
        )

    def test_missing_optional_cgroup_namespace_passes_and_is_sealed(self):
        helper = sealed_namespace_helper()
        identities = {
            name: f"{name}:[40265318{index:02d}]"
            for index, name in enumerate(
                ("ipc", "mnt", "net", "pid", "user", "uts")
            )
        }

        def readlink(path):
            name = path.rsplit("/", 1)[-1]
            return identities[name]

        with (
            mock.patch.object(os, "readlink", side_effect=readlink),
            mock.patch.object(os.path, "lexists", return_value=False),
        ):
            value = helper()
        self.assertTrue(value["mandatory_complete"])
        self.assertEqual(set(value["mandatory"]), set(identities))
        self.assertEqual(
            value["optional"]["cgroup"],
            {
                "supported": False,
                "path_absent": True,
                "self_path": "/proc/self/ns/cgroup",
                "init_path": "/proc/1/ns/cgroup",
                "self_identity": None,
                "init_identity": None,
            },
        )
        self.assertEqual(
            value["cgroup_binding_fallback"],
            "/proc/self/cgroup_plus_systemd_ControlGroup",
        )

    def test_missing_mandatory_namespace_fails_as_authority_error(self):
        helper = sealed_namespace_helper()

        def readlink(path):
            name = path.rsplit("/", 1)[-1]
            if name == "net":
                raise FileNotFoundError(path)
            return f"{name}:[4026531839]"

        with (
            mock.patch.object(os, "readlink", side_effect=readlink),
            mock.patch.object(os.path, "lexists", return_value=False),
        ):
            with self.assertRaisesRegex(
                AuthorityError, "mandatory namespace readback failed: net"
            ):
                helper()

    def test_authority_compiles_and_wraps_process_cgroup_errors(self):
        source = builder.authority_payload().decode("utf-8")
        compile(source, builder.AUTHORITY, "exec")
        self.assertIn(
            'raise AuthorityError("process cgroup readback failed") from exc',
            source,
        )
        self.assertNotIn(
            'for name in ("pid", "mnt", "net", "user", "ipc", "uts", "cgroup")',
            source,
        )

    def test_08f1_attempt_is_zero_credit_and_no_rerun(self):
        builder.verify_inputs()
        invalid = builder.load(builder.INVALIDATION)
        self.assertEqual(invalid["bundle_id"], builder.PREDECESSOR_ID)
        self.assertFalse(invalid["container_started"])
        self.assertFalse(invalid["database_started"])
        self.assertFalse(invalid["workload_started"])
        self.assertEqual(invalid["accepted_rounds_credit"], 0)
        self.assertFalse(invalid["replay_or_rerun_authorized"])


if __name__ == "__main__":
    unittest.main()
