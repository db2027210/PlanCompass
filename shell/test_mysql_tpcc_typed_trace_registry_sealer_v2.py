#!/usr/bin/env python3
"""Offline fail-closed checks for the TPC-C registry-v2 sealer."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
PACKAGED_SOURCE = HERE.parent / "registry/mysql_tpcc_typed_trace_registry_sealer_v2.py"
SOURCE = (
    PACKAGED_SOURCE
    if PACKAGED_SOURCE.is_file()
    else HERE / "mysql_tpcc_typed_trace_registry_sealer_v2.py"
)
SPEC = importlib.util.spec_from_file_location("tpcc_registry_sealer", SOURCE)
assert SPEC and SPEC.loader
SEALER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEALER)


class RegistrySealerTests(unittest.TestCase):
    def test_qps_is_rejected_recursively(self) -> None:
        with self.assertRaises(SEALER.SealError):
            SEALER.reject_qps({"nested": [{"throughput": 1}]})

    def test_non_v13_bundle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises((SEALER.SealError, FileNotFoundError)):
                SEALER.verify_v13(Path(directory))

    def test_sidecar_must_match_exact_bytes_and_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            sidecar = Path(str(path) + ".sha256")
            sidecar.write_text("0" * 64 + "  wrong-name\n", encoding="ascii")
            with self.assertRaises(SEALER.SealError):
                SEALER.verify_sidecar(path, "trace")
            sidecar.write_text(hashlib.sha256(path.read_bytes()).hexdigest() + "  trace.jsonl\n", encoding="ascii")
            SEALER.verify_sidecar(path, "trace")

    def test_owner_authority_requires_the_trace_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "trace.jsonl"
            trace.write_text(json.dumps({"capture_token": "a" * 16, "manifest_sha256": "b" * 64, "qps_included": False}) + "\n", encoding="utf-8")
            authority = root / "authority.json"
            authority.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(SEALER.SealError):
                SEALER.owner_authority(authority, trace, "b" * 64, "a" * 16)

    def test_dynamic_gate_contract_is_raw_closure_and_attempt_token_bound(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn('gate.get("attempt_token") == attempt', source)
        self.assertIn('dynamic_gate_raw_evidence.json', source)
        self.assertIn('dynamic_gate_A_solo.json', source)
        self.assertIn('mysql_algorithm_c_dynamic_gate_raw_evidence_closure_v1', source)


if __name__ == "__main__":
    unittest.main()
