#!/usr/bin/env python3
"""Focused standard-library tests for the current-attempt TPC-C materializer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve()
SOURCE = HERE.with_name("mysql_tpcc_v13_same_live_current_materializer_v2.py")
if not SOURCE.is_file():
    SOURCE = HERE.parents[1] / "materializer/mysql_tpcc_v13_same_live_current_materializer_v2.py"
SPEC = importlib.util.spec_from_file_location("tpcc_current", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


ATTEMPT = "tpcc-dd-af4819e35f81d17f2d82560b"
BUNDLE = "2" * 64
DB = "a" * 64
CLIENT = "b" * 64
IMAGE = "sha256:" + "c" * 64
PHASES = ["prepare-copy", "measurement-bridge", "provision-password", "materialize", "plan", "start", "blkio", "adopt", "prefault"]


def write_json(path: Path, value: object, *, sidecar: bool = False) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    if sidecar:
        path.with_name(path.name + ".sha256").write_text(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n", encoding="ascii"
        )


class CurrentAttemptMaterializerTest(unittest.TestCase):
    def inputs(self, root: Path) -> list[str]:
        materials = {
            "schema_version": "mysql_tpcc_v13_dedicated_materials_v2", "status": "ISSUED_NOT_EXECUTED",
            "formal_scope": "mysql_only", "qualification_only": True,
            "formal_launch_authorized": False, "formal_r0_authorized": False,
            "accepted_round_authorized": False, "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True, "attempt_id": ATTEMPT,
            "bundle_id": BUNDLE, "mysql_image_id": IMAGE, "stage_order": PHASES,
            "stages": {phase: {} for phase in PHASES},
        }
        materials_path = root / "materials.json"; write_json(materials_path, materials, sidecar=True)
        db = {"Id": DB, "Image": IMAGE, "State": {"Running": True, "Pid": 44, "StartedAt": "2026-07-31T00:00:00Z"}, "Config": {}, "HostConfig": {}, "Mounts": [{}]}
        client = {"Id": CLIENT, "State": {"Running": True, "Pid": 45, "StartedAt": "2026-07-31T00:00:00Z"}, "Config": {}, "HostConfig": {}, "Mounts": [{}]}
        db_path = root / "db.json"; client_path = root / "client.json"; write_json(db_path, [db]); write_json(client_path, [client])
        identities = {"database": {"container_id": DB, "pid": 44}, "client": {"container_id": CLIENT, "pid": 45}}
        runtime_path = root / "runtime.json"; write_json(runtime_path, {"schema_version": "mysql_tpcc_same_live_runtime_readback_v1", "capture_mode": "remote_read_only", "attempt_id": ATTEMPT, "old_workload_pids": [], "database": dict(identities["database"], process_start_ticks=101, cgroup_version=1, cgroup_path="/docker/db"), "client": dict(identities["client"], process_start_ticks=102, cgroup_version=1, cgroup_path="/docker/client")})
        # The repository has no current remote phase-ledger schema sample.
        # It is intentionally opaque here: the materializer may bind its
        # hash, but must not invent a fixed nine-phase interpretation.
        ledger_path = root / "ledger.json"; write_json(ledger_path, {"remote_read_only": True, "attempt_id": ATTEMPT, "bundle_id": BUNDLE, "materials_sha256": hashlib.sha256(materials_path.read_bytes()).hexdigest(), "old_workload_pids": []})
        return ["--materials", str(materials_path), "--db-inspect", str(db_path), "--client-inspect", str(client_path), "--runtime-readback", str(runtime_path), "--phase-ledger", str(ledger_path), "--output-root", str(root / "out")]

    def test_current_attempt_is_derived_from_materials_and_is_non_authorizing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            arguments = self.inputs(Path(temporary))
            self.assertEqual(MODULE.main(arguments), 0)
            authority = next((Path(temporary) / "out").glob("*/same_live_continuation_authority.json"))
            result = json.loads(authority.read_text(encoding="utf-8"))
            self.assertEqual(result["attempt_id"], ATTEMPT)
            self.assertFalse(result["qualification_authorized"])
            self.assertFalse(result["capture_authorized"])
            self.assertFalse(result["accepted_round_authorized"])
            self.assertFalse(result["phase_ledger_semantics_verified"])
            self.assertFalse(result["typed_capture_evidence_verified"])
            self.assertEqual(MODULE.main(arguments), 0)

    def test_mismatched_runtime_attempt_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); arguments = self.inputs(root)
            runtime = root / "runtime.json"; value = json.loads(runtime.read_text(encoding="utf-8")); value["attempt_id"] = "wrong"; write_json(runtime, value)
            with self.assertRaises(MODULE.MaterializationError):
                MODULE.main(arguments)

    def test_legacy_attempt_is_not_embedded_in_successor_source(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("tpcc-v4-v13-typed-authority", text)
        self.assertNotIn("7f2419e89da29daf29e5935d", text)

    def test_qps_in_phase_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); arguments = self.inputs(root)
            ledger = root / "ledger.json"; value = json.loads(ledger.read_text(encoding="utf-8")); value["qps"] = 1.0; write_json(ledger, value)
            with self.assertRaisesRegex(MODULE.MaterializationError, "QPS/throughput"):
                MODULE.main(arguments)

    def test_material_stage_order_is_not_reinterpreted_as_remote_ledger_schema(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("mysql_tpcc_same_live_phase_ledger_v1", source)
        self.assertIn("phase_ledger_semantics_verified\": False", source)


if __name__ == "__main__":
    unittest.main()
