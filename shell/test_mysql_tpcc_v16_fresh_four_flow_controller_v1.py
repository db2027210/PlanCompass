from __future__ import annotations
from datetime import datetime, timezone
import importlib.util, json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT/"shell/mysql_tpcc_v16_fresh_four_flow_controller_v1.py"
S=importlib.util.spec_from_file_location("fourflow",SOURCE);m=importlib.util.module_from_spec(S);S.loader.exec_module(m)
def file(path,value=b"x",side=False):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
 if side:Path(str(path)+".sha256").write_text(m.sha(path)+"  "+path.name+"\n")
 return path
def base(tmp_path):
 run=tmp_path/"new-run";python=str(tmp_path/m.PY_TREE/"bin/python3.11");entry=str(tmp_path/"collector/qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py")
 for name in ("service.json","ledger.json","a.cmd","a.auth","b.cmd","b.auth","a.cnf","b.cnf","token"):
  file(tmp_path/name,b"x",name.endswith(("cmd","auth")))
 common=lambda lane:["--bundle",str(tmp_path/("tpcc" if lane=="B" else "tpch")),"--container",("b"*64 if lane=="B" else "c"*64),"--mysql-client-container",("d"*64 if lane=="B" else "e"*64),"--mysql-defaults-file",str(tmp_path/("b.cnf" if lane=="B" else "a.cnf")),"--main-agent-token-file",str(tmp_path/"token"),"--main-agent-authority",str(tmp_path/("b.auth" if lane=="B" else "a.auth")),"--command-spec",str(tmp_path/("b.cmd" if lane=="B" else "a.cmd"))]
 def flow(lane,mode,label):return ["/usr/bin/taskset","-c",("8-15,44-51" if lane=="B" else "0-7,36-43"),python,entry,mode,*common(lane),"--output",str(run/(label+".evidence.json"))]
 def read(lane):return ["/usr/bin/taskset","-c",("8-15,44-51" if lane=="B" else "0-7,36-43"),python,entry,"--collect-only",*common(lane),"--output",str(run/(lane+"_readback.json"))]
 commands={"A_solo":flow("A","--qualification-solo","A_solo"),"B_solo":flow("B","--qualification-solo","B_solo"),"A_paired":flow("A","--qualification-paired","A_paired"),"B_paired":flow("B","--qualification-paired","B_paired"),"A_readback":read("A"),"B_readback":read("B")}
 commands["A_paired"] += ["--peer-readback",str(run/"B_readback.json"),"--peer-container","b"*64]
 commands["B_paired"] += ["--peer-readback",str(run/"A_readback.json"),"--peer-container","c"*64]
 commands["compare"]=[python,entry,"--compare-bidirectional","--a-solo-evidence",str(run/"A_solo.evidence.json"),"--a-paired-evidence",str(run/"A_paired.evidence.json"),"--b-solo-evidence",str(run/"B_solo.evidence.json"),"--b-paired-evidence",str(run/"B_paired.evidence.json"),"--output",str(run/"dynamic_gate.tpcc.json"),"--gate-scenario","tpcc_cpu","--host-id","node04","--attempt-token","fresh-four-flow-token-1234"]
 snap=lambda stage:[python,str(tmp_path/"a336/control/mysql_tpcc_v16_same_live_gate_control_v1.py"),"snapshot","--bundle",str(tmp_path/"tpcc"),"--adopted-service",str(tmp_path/"service.json"),"--phase-ledger",str(tmp_path/"ledger.json"),"--output-root",str(run/(stage+"_snapshot"))]
 commands["pre_snapshot"]=snap("pre");commands["post_snapshot"]=snap("post")
 draft={"schema_version":"mysql_tpcc_v16_fresh_four_flow_draft_v1","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_rounds_credit":0,"a336_bundle":str(tmp_path/"a336"),"v16_bundle":str(tmp_path/"tpcc"),"initial_a336_snapshot":str(tmp_path/"snapshot.json"),"python":{"path":python,"binary_sha256":m.PY_SHA,"tree_content_sha256":m.PY_TREE},"collector_bundle":str(tmp_path/"collector"),"collector_entry_relative":"qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py","run_root":str(run),"lanes":{"A":{"scenario":"tpch_memory","bundle_path":str(tmp_path/"tpch"),"bundle_id":"f"*64,"database_container_id":"c"*64,"client_container_id":"e"*64,"database_cpuset":"18-20,54-56","client_cpuset":"0-7,36-43","device":"/dev/nvme0n1","device_major_minor":"259:1","mysql_port":65103},"B":{"scenario":"tpcc_cpu","bundle_id":m.V16,"database_container_id":"b"*64,"client_container_id":"d"*64,"database_cpuset":m.DB_CPUSET,"client_cpuset":m.CLIENT_CPUSET,"device":m.DEVICE,"device_major_minor":m.MM,"mysql_port":m.PORT}},"commands":commands}
 snapv={"database":{"container_id":"b"*64,"cpuset_cpus":m.DB_CPUSET,"device":m.DEVICE,"device_major_minor":m.MM,"mysql_port":m.PORT},"client":{"container_id":"d"*64,"cpuset_cpus":m.CLIENT_CPUSET}}
 return draft,snapv,python,entry,run
def test_flow_binds_current_snapshot_and_new_output(tmp_path):
 d,s,p,e,r=base(tmp_path);out=m.verify_flow(d["commands"]["B_solo"],"B_solo",d,s,p,e,r);assert out==r/"B_solo.evidence.json"
 d["commands"]["B_solo"][d["commands"]["B_solo"].index("--container")+1]="9"*64
 with pytest.raises(m.Error):m.verify_flow(d["commands"]["B_solo"],"B_solo",d,s,p,e,r)
def test_paired_commands_are_bidirectional_and_disjoint(tmp_path):
 d,s,p,e,r=base(tmp_path);m.verify_flow(d["commands"]["A_paired"],"A_paired",d,s,p,e,r);m.verify_flow(d["commands"]["B_paired"],"B_paired",d,s,p,e,r)
 assert not (m.cpus(d["lanes"]["A"]["database_cpuset"]) & m.cpus(m.DB_CPUSET))
def test_readback_and_compare_paths_are_exact(tmp_path):
 d,s,p,e,r=base(tmp_path);assert m.verify_readback(d["commands"]["B_readback"],"B",d,s,p,e,r)==r/"B_readback.json";m.verify_compare(d["commands"]["compare"],p,e,r)
def test_qps_and_obsolete_v13_authority_rejected():
 with pytest.raises(m.Error):m.reject_qps({"resource":{"QPS":1}},"fixture")
 with pytest.raises(m.Error):m.reject_static_pid({"database_pid":96504},"fixture")
 with pytest.raises(m.Error):m.reject_static_pid(["--pid","96504"],"fixture")
 assert all(x not in Path(SOURCE).read_text() for x in ("DB_PID=96504","CLIENT_PID=96615"))
def test_execution_order_has_serial_solos_parallel_pair_and_post_snapshot():
 text=Path(SOURCE).read_text();assert 'run(commands["A_solo"]' in text and 'run(commands["B_solo"]' in text
 assert 'run_parallel((("A_paired"' in text and 'run(commands["post_snapshot"]' in text
