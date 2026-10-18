from __future__ import annotations
import hashlib, importlib.util, json, tempfile, unittest
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/"shell/mysql_tpcc_v16_registry_sealer_v1.py"
spec=importlib.util.spec_from_file_location("registry",SOURCE);R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
V16=ROOT/"artifacts/mysql_tpcc_prefault_successor_v16"/R.V16_ID;GATE=ROOT/"artifacts/mysql_tpcc_v16_same_live_gate_successor_v1"/R.GATE_ID
CDIR=V16/"authority"/("collector_payload_"+R.COLLECTOR_ID)
cspec=importlib.util.spec_from_file_location("portable",CDIR/"tpcc_typed_collector.py");C=importlib.util.module_from_spec(cspec);cspec.loader.exec_module(C);sys.modules["tpcc_typed_collector"]=C
tspec=importlib.util.spec_from_file_location("portable_test",CDIR/"test_tpcc_typed_collector_portable_v2.py");T=importlib.util.module_from_spec(tspec);tspec.loader.exec_module(T)
def seal(p,v):p.write_bytes(R.canonical(v));Path(str(p)+".sha256").write_text(hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.name+"\n")
def fixture(root):
 identity={"database":{"container_id":"d"*64},"client":{"container_id":"c"*64}};pre_snap=dict(identity,collected="before");psp=root/"pre-snapshot.json";seal(psp,pre_snap);post_snap=dict(identity,collected="after");sp=root/"post-snapshot.json";seal(sp,post_snap)
 inputs={"base_manifest_sha256":R.V16_SHA,"binding_manifest_sha256":R.BINDING_SHA,"adopted_service_sha256":"4"*64,"phase_ledger_sha256":"5"*64,"dynamic_gate_sha256":"1"*64,"dynamic_gate_raw_sha256":"2"*64,"flows":{x:"3"*64 for x in ("A_solo","A_paired","B_solo","B_paired")}}
 common={"schema_version":"mysql_tpcc_v16_same_live_typed_gate_authority_v1","bundle_id":R.V16_ID,"attempt_id":R.ATTEMPT,"formal_scope":"mysql_only","postgresql_mutation_prohibited":True,"accepted_round_authorized":False,"accepted_rounds_credit":0,"algorithm_c_unchanged":True,"same_live_identity":identity}
 pre=dict(common,status="PASS_SAME_LIVE_TYPED_CAPTURE_READY_ZERO_CREDIT",capture_authorized=True,registry_ready=False,collector_validation_sha256=None,inputs=dict(inputs,same_live_snapshot_sha256=R.sha(psp)));pp=root/"pre.json";seal(pp,pre)
 trace=root/"trace.jsonl";T.write_trace(trace);out=root/"collector";result=C.collect_and_validate(CDIR/"tpcc_offline_binding_manifest.json",trace);C.write_result(result,out)
 validation=out/"tpcc_agent_validation.json";post=dict(common,status="PASS_SAME_LIVE_REGISTRY_READY_ZERO_CREDIT",capture_authorized=False,registry_ready=True,collector_validation_sha256=R.sha(validation),inputs=dict(inputs,same_live_snapshot_sha256=R.sha(sp)));op=root/"post.json";seal(op,post)
 cmd={"phase":"typed_capture","bundle_id":R.V16_ID,"attempt_id":R.ATTEMPT,"capture_only":True,"accepted_round_authorized":False,"qps_resource_input_prohibited":True,"capture_authority_binding":{"schema_version":"mysql_tpcc_v16_capture_authority_binding_v1","capture_gate_authority_sha256":R.sha(pp),"same_live_snapshot_sha256":R.sha(psp),"v16_manifest_sha256":R.V16_SHA,"a336_manifest_sha256":R.GATE_SHA}};cmd["command_spec_sha256"]=hashlib.sha256(R.canonical(cmd)).hexdigest();cp=root/"command.json";seal(cp,cmd)
 return pp,op,psp,sp,cp,trace,out
def args(root):
 pp,op,psp,sp,cp,tr,out=fixture(root)
 return type("A",(),dict(v16_bundle=V16,gate_bundle=GATE,precapture_authority=pp,postcapture_authority=op,precapture_snapshot=psp,postcapture_snapshot=sp,typed_command=cp,trace=tr,collector_output=out,output_root=root/"registry"))()
class Tests(unittest.TestCase):
 def test_seals_and_is_idempotent(self):
  with tempfile.TemporaryDirectory() as d:
   a=args(Path(d));first=R.publish(a);second=R.publish(a);self.assertEqual(first["bundle_id"],second["bundle_id"]);self.assertEqual(second["status"],"PASS_IDEMPOTENT");m=json.loads((Path(first["bundle"])/"bundle_manifest.json").read_text());self.assertEqual(m["accepted_rounds_credit"],0);self.assertTrue(m["typed_transaction_l2_l3_complete"])
 def test_rejects_post_gate_not_bound_to_collector(self):
  with tempfile.TemporaryDirectory() as d:
   a=args(Path(d));v=json.loads(a.postcapture_authority.read_text());v["collector_validation_sha256"]="0"*64;seal(a.postcapture_authority,v)
   with self.assertRaisesRegex(R.SealError,"authority differs"):R.publish(a)
 def test_rejects_command_without_adapter(self):
  with tempfile.TemporaryDirectory() as d:
   a=args(Path(d));v=json.loads(a.typed_command.read_text());v.pop("capture_authority_binding");v.pop("command_spec_sha256");v["command_spec_sha256"]=hashlib.sha256(R.canonical(v)).hexdigest();seal(a.typed_command,v)
   with self.assertRaisesRegex(R.SealError,"adapter"):R.publish(a)
 def test_rejects_qps_resource_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   a=args(Path(d));v=json.loads(a.postcapture_snapshot.read_text());v["qps"]=1;seal(a.postcapture_snapshot,v);post=json.loads(a.postcapture_authority.read_text());post["inputs"]["same_live_snapshot_sha256"]=R.sha(a.postcapture_snapshot);seal(a.postcapture_authority,post)
   with self.assertRaisesRegex(R.SealError,"QPS"):R.publish(a)
if __name__=="__main__":unittest.main()
