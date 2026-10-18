#!/usr/bin/env python3
"""Offline negative/primitive-positive checks for postcapture materialization."""
from __future__ import annotations
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

SOURCE=Path(__file__).with_name("mysql_tpcc_v13_postcapture_evidence_materializer_v1.py")
SPEC=importlib.util.spec_from_file_location("postcapture",SOURCE);assert SPEC and SPEC.loader
MOD=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(MOD)

class Tests(unittest.TestCase):
 def test_canonical_identity_is_stable(self):
  self.assertEqual(MOD.canon({"b":1,"a":2}),b'{"a":2,"b":1}\n')
 def test_bad_sidecar_is_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/"x";p.write_text("x");Path(str(p)+".sha256").write_text("0"*64+"  x\n")
   with self.assertRaises(MOD.Error):MOD.sidecar(p,"x")
 def test_non_derivable_evidence_is_explicit(self):
  text=SOURCE.read_text()
  for name in ("typed_capture_result","production_trace_provenance","distributed_dynamic_gate_raw_four_inputs"):
   self.assertIn(name,text)
if __name__=="__main__":unittest.main()
