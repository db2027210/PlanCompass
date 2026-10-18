#!/usr/bin/env python3
"""Offline tests for manifest-files-bound TPC-C phase repair verification."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve()
SOURCE = HERE.with_name("mysql_tpcc_v13_phase_authority_repair_verifier_v1.py")
if not SOURCE.is_file():
    SOURCE = HERE.parents[1] / "authority/mysql_tpcc_v13_phase_authority_repair_verifier_v1.py"
SPEC = importlib.util.spec_from_file_location("tpcc_repair", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ManifestMemberTest(unittest.TestCase):
    def test_contract_hash_comes_from_files_not_legacy_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = root / "authority/four_scene_successor_contract.json"
            contract.parent.mkdir()
            contract.write_text("{}\n", encoding="utf-8")
            actual = hashlib.sha256(contract.read_bytes()).hexdigest()
            # An intentionally wrong legacy root must not replace the member
            # binding.  The member is the sole authority input.
            manifest = {"files": {MODULE.CONTRACT: actual}, "four_scene_contract_sha256": "0" * 64}
            self.assertEqual(MODULE.verify_manifest_contract_member(root, manifest), actual)

    def test_missing_manifest_member_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(MODULE.VerificationError, "does not bind"):
                MODULE.verify_manifest_contract_member(Path(temporary), {"files": {}})

    def test_tampered_member_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / MODULE.CONTRACT
            path.parent.mkdir(); path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.VerificationError, "differs"):
                MODULE.verify_manifest_contract_member(root, {"files": {MODULE.CONTRACT: "a" * 64}})


if __name__ == "__main__":
    unittest.main()
