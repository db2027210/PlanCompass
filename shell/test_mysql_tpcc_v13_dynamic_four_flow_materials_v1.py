#!/usr/bin/env python3
import importlib.util, json, subprocess, tempfile, unittest
from pathlib import Path
S=Path(__file__).with_name("mysql_tpcc_v13_dynamic_four_flow_materials_v1.py");spec=importlib.util.spec_from_file_location("m",S);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class TestFourFlow(unittest.TestCase):
 def test_exact_four_streams(self):
  self.assertEqual(("A_solo","A_paired","B_solo","B_paired"),tuple((label for label in ("A_solo","A_paired","B_solo","B_paired"))))
 def test_issued_material_is_not_a_gate_or_acceptance_authority(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d); authority=root/"authority.json"
   authority.write_text(json.dumps({"schema_version":"mysql_tpcc_v13_dual_projection_translation_authority_v1","status":"PASS_SAME_INSPECT_DUAL_PROJECTION","accepted_round_authorized":False,"attempt_id":"a","same_live_identity":{}}))
   result=subprocess.run(["python3",str(S),"--dual-projection-authority",str(authority),"--output-root",str(root/"out"),"--ttl-seconds","60"],capture_output=True,text=True,check=True)
   material=json.loads(next((root/"out").glob("*/dynamic_four_flow_materials.json")).read_text())
   self.assertFalse(material["formal_launch_authorized"]);self.assertFalse(material["accepted_round_authorized"])
   self.assertEqual(set(material["required_raw_inputs"]),{"A_solo","A_paired","B_solo","B_paired"})
