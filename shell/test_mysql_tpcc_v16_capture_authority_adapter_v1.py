from __future__ import annotations
import hashlib, importlib.util, json, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SOURCE=ROOT/"shell/mysql_tpcc_v16_capture_authority_adapter_v1.py"
spec=importlib.util.spec_from_file_location("capture_adapter",SOURCE);A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)
V16=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/A.V16_ID
JOINT=ROOT/"artifacts/mysql_tpcc_joint_successor_v1"/A.JOINT_ID
GATE=ROOT/"artifacts/mysql_tpcc_v16_same_live_gate_successor_v1"/A.GATE_ID
def seal(p,v):
 p.write_bytes(A.canonical(v));Path(str(p)+".sha256").write_text(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n")
def fixtures(root):
 db={"container_id":"d"*64,"pid":123,"process_start_ticks":456,"datadir":"/mnt/x","device_major_minor":"259:2","cpuset_cpus":"21-24,57-60","cpuset_mems":"0","mysql_port":65106};cl={"container_id":"c"*64,"pid":789,"process_start_ticks":1011}
 snap={"schema_version":"mysql_tpcc_v16_same_live_readback_v1","status":"PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY","bundle_id":A.V16_ID,"attempt_id":A.ATTEMPT,"collected_at_utc":datetime.now(timezone.utc).isoformat(),"database":db,"client":cl,"postgresql_mutation_prohibited":True};sp=root/"snapshot.json";seal(sp,snap)
 inner={"status":"PASS_FOUR_FLOWS_FRESH_GATE_ZERO_CREDIT","evidence_sha256":{x:x[0].lower()*64 for x in ("A_solo","A_paired","B_solo","B_paired")},"dynamic_gate_sha256":"9"*64};ip=root/"inner.json";seal(ip,inner)
 joint={"schema_version":"mysql_tpcc_joint_four_flow_completion_v1","status":"PASS_ALL_INPUTS_AND_FOUR_FLOWS_BOUND_ZERO_CREDIT","inner_completion_sha256":A.sha(ip),"inner_evidence_sha256":inner["evidence_sha256"],"inner_dynamic_gate_sha256":inner["dynamic_gate_sha256"],"formal_scope":"mysql_only","postgresql_mutation_prohibited":True,"accepted_round_authorized":False,"accepted_rounds_credit":0};jp=root/"joint.json";seal(jp,joint)
 gate={"schema_version":"mysql_tpcc_v16_same_live_typed_gate_authority_v1","status":"PASS_SAME_LIVE_TYPED_CAPTURE_READY_ZERO_CREDIT","bundle_id":A.V16_ID,"attempt_id":A.ATTEMPT,"same_live_identity":{"database":db,"client":cl},"inputs":{"same_live_snapshot_sha256":A.sha(sp),"dynamic_gate_sha256":inner["dynamic_gate_sha256"],"flows":inner["evidence_sha256"]},"capture_authorized":True,"registry_ready":False,"collector_validation_sha256":None,"algorithm_c_unchanged":True,"accepted_round_authorized":False,"accepted_rounds_credit":0};gp=root/"gate.json";seal(gp,gate)
 cmd={"schema_version":"mysql_tpcc_materialized_phase_command_v1","status":"SEALED_CAPTURE_COMMAND","phase":"typed_capture","bundle_id":A.V16_ID,"attempt_id":A.ATTEMPT,"capture_only":True,"formal_authorized":False,"accepted_round_authorized":False,"qps_resource_input_prohibited":True,"db_identity":db,"client_container":cl};cmd["command_spec_sha256"]=hashlib.sha256(A.canonical(cmd)).hexdigest();cp=root/"cmd.json";seal(cp,cmd)
 return jp,ip,sp,gp,cp
class Tests(unittest.TestCase):
 def test_binds_gate_and_recomputes_command_spec(self):
  with tempfile.TemporaryDirectory() as d:
   jp,ip,sp,gp,cp=fixtures(Path(d));out=A.adapt(v16_bundle=V16,joint_bundle=JOINT,gate_bundle=GATE,joint_completion=jp,inner_completion=ip,snapshot=sp,capture_gate=gp,materialized_command=cp)
   u=dict(out);digest=u.pop("command_spec_sha256");self.assertEqual(digest,hashlib.sha256(A.canonical(u)).hexdigest());self.assertEqual(out["capture_authority_binding"]["joint_completion_sha256"],A.sha(jp));self.assertEqual(out["accepted_round_authorized"],False)
 def test_rejects_joint_dynamic_mismatch(self):
  with tempfile.TemporaryDirectory() as d:
   jp,ip,sp,gp,cp=fixtures(Path(d));g=json.loads(gp.read_text());g["inputs"]["dynamic_gate_sha256"]="0"*64;seal(gp,g)
   with self.assertRaisesRegex(A.AuthorityError,"closure"):A.adapt(v16_bundle=V16,joint_bundle=JOINT,gate_bundle=GATE,joint_completion=jp,inner_completion=ip,snapshot=sp,capture_gate=gp,materialized_command=cp)
 def test_rejects_registry_ready_instead_of_capture_gate(self):
  with tempfile.TemporaryDirectory() as d:
   jp,ip,sp,gp,cp=fixtures(Path(d));g=json.loads(gp.read_text());g["registry_ready"]=True;seal(gp,g)
   with self.assertRaises(A.AuthorityError):A.adapt(v16_bundle=V16,joint_bundle=JOINT,gate_bundle=GATE,joint_completion=jp,inner_completion=ip,snapshot=sp,capture_gate=gp,materialized_command=cp)
 def test_rejects_qps_anywhere(self):
  with tempfile.TemporaryDirectory() as d:
   jp,ip,sp,gp,cp=fixtures(Path(d));c=json.loads(cp.read_text());c["qps"]=1;c.pop("command_spec_sha256");c["command_spec_sha256"]=hashlib.sha256(A.canonical(c)).hexdigest();seal(cp,c)
   with self.assertRaises(A.AuthorityError):A.adapt(v16_bundle=V16,joint_bundle=JOINT,gate_bundle=GATE,joint_completion=jp,inner_completion=ip,snapshot=sp,capture_gate=gp,materialized_command=cp)
if __name__=="__main__":unittest.main()
