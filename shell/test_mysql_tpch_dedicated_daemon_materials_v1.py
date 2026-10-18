#!/usr/bin/env python3
"""Offline tests for dedicated-daemon rendering and launch materials."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
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


renderer = import_path(
    "tpch_dedicated_renderer",
    HERE / "render_mysql_tpch_dedicated_daemon_bootstrap_spec_v1.py",
)
materials = import_path(
    "tpch_dedicated_materials",
    HERE / "mysql_tpch_dedicated_daemon_materials_v1.py",
)


class DedicatedMaterialsTests(unittest.TestCase):
    def test_renderer_binds_daemon_and_direct_host_execution(self):
        manifest = renderer.verify_successor(renderer.DEFAULT_BUNDLE_MANIFEST)
        self.assertEqual(manifest["bundle_id"], renderer.BUNDLE_ID)
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "token"
            token.write_text("x" * 48 + "\n", encoding="ascii")
            os.chmod(token, 0o600)
            document = renderer.render(argparse.Namespace(
                bundle_manifest=renderer.DEFAULT_BUNDLE_MANIFEST,
                mysql_image_reference=materials.MYSQL_REFERENCE,
                mysql_image_id=materials.MYSQL_IMAGE_ID,
                client_image_reference=materials.MYSQL_REFERENCE,
                client_image_id=materials.MYSQL_IMAGE_ID,
                main_agent_token_file=token,
                attempt_token=(
                    "tpch-v2-20260731t000000z-"
                    "0123456789abcdef01234567"
                ),
                valid_minutes=25,
                output=Path(temporary) / "unused.json",
            ))
        self.assertEqual(
            document["dedicated_daemon"]["socket"],
            "/run/ra-docker-mysql-tpch-v1.sock",
        )
        self.assertEqual(
            document["bootstrap_execution"]["mode"],
            "direct_host_systemd_transient_oneshot",
        )
        self.assertEqual(document["bootstrap_execution"]["ppid"], 1)
        self.assertEqual(
            document["bootstrap_execution"]["service_type"], "oneshot"
        )
        self.assertTrue(
            document["bootstrap_execution"]["remain_after_exit"]
        )

    def test_material_plan_never_places_token_content_in_argv(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = materials.create(
                materials.DEFAULT_BUNDLE, Path(temporary) / "out"
            )
            directory = Path(result["materials_directory"])
            verified = materials.verify_materials(directory)
            plan = json.loads(
                (directory / "command_plan.json").read_text(encoding="utf-8")
            )
            token = (
                directory / "main_agent_token"
            ).read_text(encoding="ascii").strip()
            argv = plan["bootstrap_launch"]["argv"]
            self.assertEqual(
                plan["bootstrap_launch"]["mode"],
                "direct_host_systemd_transient_oneshot",
            )
            self.assertIn("--main-agent-token-file", argv)
            self.assertNotIn("--main-agent-token", argv)
            self.assertNotIn("--wait", argv)
            self.assertNotIn("--collect", argv)
            self.assertNotIn(token, argv)
            installation = plan["remote_authority_installation"]
            token_gate = installation["main_agent_token"]
            self.assertEqual(token_gate["owner"], "root:root")
            self.assertEqual(token_gate["mode"], "0600")
            self.assertEqual(
                token_gate["source_sha256"],
                materials.sha256(directory / "main_agent_token"),
            )
            self.assertTrue(token_gate["remote_sha256_must_equal_source"])
            self.assertEqual(
                installation["readonly_audit_files"]["mode"], "0444"
            )
            self.assertTrue(plan["unique_final_materials"])
            self.assertEqual(
                plan["target_host"],
                {"hostname": "node04", "address": "192.0.2.7"},
            )
            envelope = plan["frozen_fresh_r0_envelope"]
            self.assertEqual(envelope["cpu_quota_cores"], 4)
            self.assertEqual(envelope["cpuset_cpus"], "18-20,54-56")
            self.assertEqual(envelope["device"], "/dev/nvme0n1")
            self.assertEqual(envelope["device_major_minor"], "259:1")
            self.assertEqual(envelope["mysqld_port"], 65103)
            self.assertEqual(envelope["io_read_bps"], 100663296)
            self.assertEqual(envelope["io_write_bps"], 100663296)
            counter_gate = plan["database_counter_timeout_gate"]
            self.assertTrue(counter_gate["fail_closed"])
            self.assertEqual(counter_gate["required_timeout_count"], 0)
            self.assertEqual(
                counter_gate["required_missing_sample_count"], 0
            )
            superseded = json.loads(
                (
                    directory / materials.SUPERSEDED_DE3E_RELATIVE
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                superseded["status"], "INVALIDATED_DO_NOT_RUN_OR_CREDIT"
            )
            self.assertEqual(superseded["accepted_rounds_credit"], 0)
            self.assertEqual(
                verified["status"], "PASS_VERIFIED_UNEXPIRED_NOT_EXECUTED"
            )
            self.assertEqual(verified["valid_minutes"], 60)

    def test_sealed_authority_rejects_anonymous_owner_and_accepts_root_owner(self):
        authority_path = (
            materials.DEFAULT_BUNDLE
            / "mysql_tpch_distributed_v2_authority.py"
        )
        tree = ast.parse(authority_path.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_read_main_agent_token_file"
        )
        namespace = {
            "Path": Path,
            "stat": stat,
            "os": os,
            "AuthorityError": RuntimeError,
        }
        exec(
            compile(
                ast.fix_missing_locations(
                    ast.Module(body=[function], type_ignores=[])
                ),
                "_read_main_agent_token_file",
                "exec",
            ),
            namespace,
        )
        read_token = namespace["_read_main_agent_token_file"]
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "main_agent_token"
            token.write_text("r" * 48 + "\n", encoding="ascii")
            os.chmod(token, 0o600)

            def owned_by(uid):
                return SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_uid=uid,
                )

            with (
                mock.patch.object(os, "geteuid", return_value=0),
                mock.patch.object(Path, "stat", return_value=owned_by(600)),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "ownership/mode differs"
                ):
                    read_token(token)
            with (
                mock.patch.object(os, "geteuid", return_value=0),
                mock.patch.object(Path, "stat", return_value=owned_by(0)),
            ):
                self.assertEqual(read_token(token), "r" * 48)

    def test_sealed_bundle_contains_no_bytecode_or_cache(self):
        prohibited = [
            path
            for path in materials.DEFAULT_BUNDLE.rglob("*")
            if path.name == "__pycache__" or path.suffix == ".pyc"
        ]
        self.assertEqual(prohibited, [])

    def test_expired_materials_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = materials.create(
                materials.DEFAULT_BUNDLE, Path(temporary) / "out"
            )
            directory = Path(result["materials_directory"])
            manifest_path = directory / "materials_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["expires_at_utc"] = "2000-01-01T00:00:00+00:00"
            os.chmod(manifest_path, 0o600)
            manifest_path.write_bytes(materials.canonical(manifest))
            with self.assertRaisesRegex(
                materials.MaterialError, "materials expired"
            ):
                materials.verify_materials(directory)


if __name__ == "__main__":
    unittest.main()
