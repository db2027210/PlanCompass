from __future__ import annotations
import hashlib,importlib.util
from datetime import datetime,timezone
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];SOURCE=(ROOT/"control/mysql_tpcc_v16_local_capture_authority_adapter_v1.py" if (ROOT/"control/mysql_tpcc_v16_local_capture_authority_adapter_v1.py").is_file() else ROOT/"shell/mysql_tpcc_v16_local_capture_authority_adapter_v1.py");spec=importlib.util.spec_from_file_location("adapter",SOURCE);M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
def seal(path,value):
 path.write_bytes(M.canonical(value));Path(str(path)+".sha256").write_text(M.sha(path)+"  "+path.name+"\n");return path
def test_qps_is_rejected_as_resource_input():
 with pytest.raises(M.Error,match="QPS"):M.reject_qps({"qps":1})
def test_local_ids_and_zero_credit_contract_are_pinned():
 text=SOURCE.read_text();assert M.FLOW_ID.startswith("8833") and 'tpcc_workloads_started":1' in text and 'tpch_workloads_started":0' in text and 'typed_transaction_l2_l3_required_before_registry":True' in text and 'accepted_rounds_credit":0' in text
def test_seal_is_fail_closed(tmp_path):
 p=seal(tmp_path/"x.json",{"x":1});assert M.sealed(p,"x")[1]=={"x":1};p.write_text('{"x":2}')
 with pytest.raises(M.Error):M.sealed(p,"x")
def test_complete_local_chain_can_bind_capture_command(monkeypatch,tmp_path):
 monkeypatch.setattr(M,"bundle",lambda *args:None);db={"container_id":"d"*64,"pid":11,"process_start_ticks":12,"datadir":"/mnt/x","device_major_minor":"259:2","cpuset_cpus":"21-24,57-60","cpuset_mems":"0","mysql_port":65106};client={"container_id":"c"*64,"pid":21,"process_start_ticks":22}
 ev={"initial_live_readback":{"bundle_id":M.V16_ID,"scenario":"tpcc_cpu","container":{"container_id":db["container_id"],"pid":db["pid"]}}};ep=seal(tmp_path/"evidence.json",ev)
 gate={"schema_version":"mysql_tpcc_v16_local_parallel_interference_gate_v1","status":"PASS_TPCC_ONLY_WITH_TPCH_READ_ONLY_STABLE","capture_authorized":True,"rolling_inventory_complete":True,"foreign_writer_count":0,"tpch_mutation_performed":False,"typed_transaction_l2_l3_required_before_registry":True,"accepted_rounds_credit":0};gp=seal(tmp_path/"gate.json",gate)
 completion={"schema_version":"mysql_tpcc_v16_local_parallel_completion_v1","status":"PASS_TPCC_LOCAL_QUALIFICATION_ZERO_CREDIT","tpcc_workloads_started":1,"tpch_workloads_started":0,"tpch_mutation_performed":False,"tpcc_evidence_sha256":M.sha(ep),"local_interference_gate_sha256":M.sha(gp),"accepted_rounds_credit":0};cp=seal(tmp_path/"completion.json",completion)
 snap={"schema_version":"mysql_tpcc_v16_same_live_readback_v1","status":"PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY","bundle_id":M.V16_ID,"attempt_id":M.ATTEMPT,"collected_at_utc":datetime.now(timezone.utc).isoformat(),"database":db,"client":client};sp=seal(tmp_path/"snapshot.json",snap)
 command={"schema_version":"mysql_tpcc_materialized_phase_command_v1","status":"SEALED_CAPTURE_COMMAND","phase":"typed_capture","bundle_id":M.V16_ID,"attempt_id":M.ATTEMPT,"capture_only":True,"formal_authorized":False,"accepted_round_authorized":False,"qps_resource_input_prohibited":True,"db_identity":db,"client_container":client};command["command_spec_sha256"]=hashlib.sha256(M.canonical(command)).hexdigest();mp=seal(tmp_path/"command.json",command)
 class A:pass
 a=A();a.v16_bundle=a.local_flow_bundle=a.a336_bundle=tmp_path;a.local_completion=cp;a.local_gate=gp;a.local_evidence=ep;a.fresh_snapshot=sp;a.materialized_command=mp
 out=M.adapt(a);assert out["capture_authority_binding"]["capture_authorized"] is True and out["capture_authority_binding"]["tpch_workloads_started"]==0
