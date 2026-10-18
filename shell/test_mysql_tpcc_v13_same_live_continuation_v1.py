#!/usr/bin/env python3
import importlib.util, unittest
from pathlib import Path
S=Path(__file__).with_name("mysql_tpcc_v13_same_live_continuation_v1.py")
spec=importlib.util.spec_from_file_location("c",S);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TestDualProjection(unittest.TestCase):
 def test_distinct_manifest_projections_are_explicit(self):
  row={"Config":{"a":1},"HostConfig":{},"Mounts":[],"NetworkSettings":{},"Image":"x","Created":"now"}
  a=m.sha_bytes(m.canon(m.projection_adopted(row)));q=m.sha_bytes(m.canon(m.projection_qualification(row)))
  self.assertNotEqual(a,q)
  self.assertEqual(list(m.projection_adopted(row)),["Config","HostConfig","Mounts","NetworkSettings"])
  self.assertEqual(list(m.projection_qualification(row)),["Config","HostConfig","Mounts","Image","Created"])
 def test_expected_hashes_are_not_equal(self):self.assertNotEqual(m.ADOPTED_HASH,m.QUALIFICATION_HASH)
 def test_canonical_is_newline_terminated_and_stable(self):self.assertEqual(m.canon({"b":1,"a":2}),b'{\n  "a": 2,\n  "b": 1\n}\n')

class TestV13CollectorSchema(unittest.TestCase):
 def test_v13_manifest_is_accepted_by_collector_verifier(self):
  q=Path(__file__).with_name("mysql_distributed_live_readback_qualification_v1.py")
  spec=importlib.util.spec_from_file_location("q",q);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  bundle=Path(__file__).parents[1]/"artifacts/mysql_tpcc_typed_distributed_successor_v13/4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
  manifest,runtime=module.verify_bundle(bundle)
  self.assertEqual(manifest["bundle_id"],bundle.name);self.assertEqual(runtime["scenario"],"tpcc_cpu")
