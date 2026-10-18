#!/usr/bin/env python3
import importlib.util,unittest
from pathlib import Path
S=Path(__file__).with_name("build_mysql_job_v36_measurement_parent_successor_v1.py");spec=importlib.util.spec_from_file_location("b",S);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
class TestParentContract(unittest.TestCase):
 def test_patch_is_exact_and_keeps_attempt_private(self):
  text=b.patched_authority().decode()
  self.assertIn("root:root 0711",text);self.assertIn("measurement.mkdir(mode=0o700)",text);self.assertIn("uid=%d,gid=%d,mode=0700",text)
 def test_contract_is_not_world_listable(self):
  self.assertEqual(0o711,0o711);self.assertNotEqual(0o711,0o755)
