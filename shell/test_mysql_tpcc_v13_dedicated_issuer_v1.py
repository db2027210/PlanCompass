#!/usr/bin/env python3
"""Self-contained tests copied into the sealed TPC-C successor bundle."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve()
BUNDLE = HERE.parents[1]
ISSUER_PATH = BUNDLE / "materials/mysql_tpcc_v13_dedicated_issuer_v1.py"
SERVICE_PATH = BUNDLE / "authority/mysql_tpcc_node04_adopted_service_v2.py"
CONFIG_PATH = BUNDLE / "authority/mysql_tpcc_config_materialize_v2.py"
MEASUREMENT_PATH = BUNDLE / "authority/mysql_tpcc_measurement_bridge_v1.py"
BLKIO_PATH = BUNDLE / "authority/mysql_tpcc_blkio_v1.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError("module cannot be loaded: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


class DedicatedIssuerTests(unittest.TestCase):
    def test_bundle_closure_and_issuer_are_self_contained(self) -> None:
        manifest_path = BUNDLE / "bundle_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(BUNDLE.name, manifest["bundle_id"])
        expected = set(manifest["files"])
        expected.update({"bundle_manifest.json", "bundle_manifest.json.sha256"})
        observed = {
            path.relative_to(BUNDLE).as_posix()
            for path in BUNDLE.rglob("*")
            if path.is_file()
        }
        self.assertEqual(expected, observed)
        for relative, wanted in manifest["files"].items():
            self.assertEqual(wanted, digest(BUNDLE / relative), relative)
        self.assertEqual(
            digest(manifest_path),
            (BUNDLE / "bundle_manifest.json.sha256")
            .read_text(encoding="ascii")
            .split()[0],
        )
        self.assertTrue(ISSUER_PATH.is_file())
        self.assertTrue(SERVICE_PATH.is_file())
        self.assertTrue(CONFIG_PATH.is_file())
        self.assertTrue(MEASUREMENT_PATH.is_file())
        self.assertTrue(BLKIO_PATH.is_file())

    def _issue(self, temporary: Path):
        issuer = load(ISSUER_PATH, "sealed_tpcc_issuer")
        token = temporary / "root-token"
        token.write_text("fixture-authority-token\n", encoding="ascii")
        token.chmod(0o600)
        destination, value = issuer.issue(
            BUNDLE / "bundle_manifest.json",
            token,
            temporary / "materials",
            60,
        )
        return issuer, destination, value

    def test_nine_real_phases_are_frozen_and_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            issuer, destination, value = self._issue(Path(directory))
            order = [
                "prepare-copy",
                "measurement-bridge",
                "provision-password",
                "materialize",
                "plan",
                "start",
                "blkio",
                "adopt",
                "prefault",
            ]
            self.assertEqual(order, value["stage_order"])
            self.assertEqual(order, list(value["stages"]))
            self.assertEqual("mysql_only", value["formal_scope"])
            self.assertTrue(value["cross_engine_comparison_prohibited"])
            self.assertFalse(value["formal_launch_authorized"])
            self.assertFalse(value["accepted_round_authorized"])
            self.assertEqual(0, value["accepted_rounds_credit"])
            self.assertEqual(issuer.MYSQL_IMAGE_ID, value["mysql_image_id"])
            self.assertEqual(str(issuer.PYTHON), value["frozen_python"]["path"])
            self.assertNotIn("<", json.dumps(value))
            for stage in value["stages"].values():
                self.assertNotIn("--wait", stage["argv"])
                self.assertNotIn("--collect", stage["argv"])
                self.assertEqual(
                    ["--wait", "--collect"],
                    stage["systemd_run"]["forbidden_options"],
                )
            root = Path(value["attempt_root"])
            self.assertEqual(
                str(root / "physical_copy_authority.json"),
                value["stages"]["prepare-copy"]["argv"][-1],
            )
            self.assertEqual(
                str(root / "measurement_bridge_authority.json"),
                value["stages"]["measurement-bridge"]["argv"][-1],
            )
            self.assertEqual(
                str(root / "credential_provisioning.json"),
                value["stages"]["provision-password"]["argv"][-1],
            )
            self.assertEqual(
                str(root / "config_materialization.json"),
                value["stages"]["materialize"]["argv"][-1],
            )
            self.assertEqual(
                str(root / "config"),
                value["stages"]["plan"]["argv"][
                    value["stages"]["plan"]["argv"].index("--config-dir") + 1
                ],
            )
            self.assertEqual(
                str(root / "blkio_throttle_authority.json"),
                value["stages"]["blkio"]["argv"][-1],
            )
            self.assertEqual(
                ["prepare-copy"],
                value["stages"]["measurement-bridge"]["dependencies"],
            )
            self.assertEqual(
                ["measurement-bridge"],
                value["stages"]["provision-password"]["dependencies"],
            )
            self.assertEqual(
                ["start"],
                value["stages"]["blkio"]["dependencies"],
            )
            self.assertEqual(
                ["blkio"],
                value["stages"]["adopt"]["dependencies"],
            )
            private = destination / "private_authority/root-token"
            self.assertEqual(0o600, stat.S_IMODE(private.stat().st_mode))
            self.assertEqual(
                value["handoff_token_sha256"],
                digest(private),
            )
            self.assertEqual(
                value["attempt_id"],
                private.read_text(encoding="ascii").strip(),
            )
            self.assertTrue(
                value["remote_install_gate"][
                    "source_token_is_issuer_authority_only"
                ]
            )

    def test_all_emitted_argv_round_trip_through_real_parsers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _issuer, _destination, value = self._issue(Path(directory))
            stages = value["stages"]
            service = load(SERVICE_PATH, "sealed_tpcc_service")
            config = load(CONFIG_PATH, "sealed_tpcc_config")
            measurement = load(MEASUREMENT_PATH, "sealed_tpcc_measurement")
            blkio = load(BLKIO_PATH, "sealed_tpcc_blkio")
            attempt_root = Path(value["attempt_root"])
            plan = {
                "database": {
                    "datadir": str(attempt_root / "datadir/datadir"),
                }
            }
            success = {
                "status": "PASS",
                "bundle_id": value["bundle_id"],
                "attempt_id": value["attempt_id"],
            }
            with (
                mock.patch.object(
                    service, "prepare_physical_copy", return_value=success
                ),
                mock.patch.object(service, "build_plan", return_value=plan),
                mock.patch.object(service, "start", return_value=success),
                mock.patch.object(service, "adopt", return_value=success),
                mock.patch.object(service, "prefault", return_value=success),
                mock.patch.object(service.base, "load", return_value=plan),
                mock.patch.object(service.base, "atomic_json"),
                mock.patch.object(Path, "read_text", return_value="token\n"),
            ):
                for name in ("prepare-copy", "plan", "start", "adopt", "prefault"):
                    argv = stages[name]["argv"][2:]
                    self.assertEqual(0, service.main(argv), name)

            with (
                mock.patch.object(config, "provision", return_value=success),
                mock.patch.object(config, "materialize", return_value=success),
            ):
                for name in ("provision-password", "materialize"):
                    argv = stages[name]["argv"][1:]
                    with mock.patch.object(sys, "argv", argv):
                        self.assertEqual(0, config.main(), name)

            measurement_success = dict(
                success,
                device_major_minor="0:42",
            )
            with mock.patch.object(
                measurement, "enforce", return_value=measurement_success
            ):
                argv = stages["measurement-bridge"]["argv"][1:]
                with mock.patch.object(sys, "argv", argv):
                    self.assertEqual(0, measurement.main())
            with mock.patch.object(blkio, "enforce", return_value=success):
                argv = stages["blkio"]["argv"][1:]
                with mock.patch.object(sys, "argv", argv):
                    self.assertEqual(0, blkio.main())

    def test_source_token_gate_rejects_broad_mode(self) -> None:
        issuer = load(ISSUER_PATH, "sealed_tpcc_issuer_mode")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "root-token"
            token.write_text("fixture\n", encoding="ascii")
            token.chmod(0o644)
            with self.assertRaises(issuer.IssuerError):
                issuer.issue(
                    BUNDLE / "bundle_manifest.json",
                    token,
                    root / "materials",
                    60,
                )


if __name__ == "__main__":
    unittest.main()
