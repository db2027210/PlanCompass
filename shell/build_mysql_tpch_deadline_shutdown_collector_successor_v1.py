#!/usr/bin/env python3
"""Rebind e34 parallel collector to the deadline-bounded TPC-H target."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve()
PARENT_ID="e34eb78feb33717bdb6e0c0a31e1f9b6f28e37f6a3779704b39e4da5d6aa9475";PARENT_SHA="7a657f3dcc3bd031f16fc38ac4612a8f58c01409cbb487d94ce4d9f9a1d1b1e9"
TARGET_ID="b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47";TARGET_SHA="386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439";TARGET_ADAPTER="5b0051d048aaf64ff0533a809e700c13f5f469f53d98143ad77e661c37fa642e"
PARENT=ROOT/"artifacts/mysql_tpch_parallel_socket_inventory_successor_v1"/PARENT_ID;TARGET=ROOT/"artifacts/mysql_tpch_deadline_shutdown_successor_v1"/TARGET_ID;OUT=ROOT/"artifacts/mysql_tpch_deadline_shutdown_collector_successor_v1"
ENTRY="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";SUPPLEMENT="qualification/mysql_tpch_legacy_blkio_supplement_v1.py";PROJECTION="authority/tpch_target_manifest.json";CURRENT_TEST="tests/test_mysql_tpch_tpcc_shared_collector_current_v1.py"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def fmap(root):return {p.relative_to(root).as_posix():sf(p) for p in sorted(root.rglob("*")) if p.is_file() and p.relative_to(root).as_posix() not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def exact(text,old,new,label):
 if text.count(old)!=1:raise BuildError(label+" anchor differs")
 return text.replace(old,new)
def patch_ids(payload):
 text=payload.decode();text=exact(text,"ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4",TARGET_ID,"target id");text=exact(text,"af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52",TARGET_SHA,"target manifest");return text.encode()
def patch_supplement(payload):
 text=patch_ids(payload).decode()
 text=exact(text,'TARGET_ATTEMPT = "tpch-v2-20260803t022500z-afbe6badff313374deadbeef"\nTARGET_CONTAINER = "ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db"\n','',"remove stale attempt/container")
 text=exact(text,'authority_path.parent.name != TARGET_ATTEMPT','authority_path.parent.name != authority.get("attempt_token")',"dynamic authority directory")
 text=exact(text,'authority.get("attempt_token") != TARGET_ATTEMPT','not isinstance(authority.get("attempt_token"), str)\n            or not authority.get("attempt_token").startswith("tpch-v2-")',"dynamic attempt")
 text=exact(text,'if container_name != TARGET_CONTAINER:\n        raise SupplementError("exact current TPC-H container name differs")','if container_name != authority.get("database_container"):\n        raise SupplementError("fresh authority database container differs")',"dynamic database")
 text=exact(text,'"attempt_token": TARGET_ATTEMPT','"attempt_token": authority["attempt_token"]',"dynamic output attempt")
 return text.replace("legacy ff313", "sealed current-target").replace("exact ff313", "exact current-target").encode()
def patch_test(payload):
 text=payload.decode();old='TPCH = ROOT / "artifacts/mysql_tpch_sql_native_percentile_successor_v1/ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"';new='TPCH = ROOT / "artifacts/mysql_tpch_deadline_shutdown_successor_v1/'+TARGET_ID+'"';return exact(text,old,new,"current target test").encode()
TEST=r'''from __future__ import annotations
import importlib.util,json
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];ROOT=Path(__file__).resolve().parents[4];E=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";s=importlib.util.spec_from_file_location("shutdown_collector",E);M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
TARGET=ROOT/"artifacts/mysql_tpch_deadline_shutdown_successor_v1/b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"
OLD=ROOT/"artifacts/mysql_tpch_sql_native_percentile_successor_v1/ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4"
def test_only_deadline_target_is_current():
 m,_=M.verify_current_target_seal(TARGET,"tpch_memory");assert m["shutdown_successor_generation"]=="deadline_bounded_query_join_v1" and m["query_client_limit_seconds"]==110
 with pytest.raises(M.core.QualificationError):M.verify_current_target_seal(OLD,"tpch_memory")
def test_target_sql_adapter_and_projection_are_exact():
 tm=json.loads((TARGET/"bundle_manifest.json").read_text());projection=json.loads((B/"authority/tpch_target_manifest.json").read_text());assert tm["component_hashes"]["sql_adapter"]=="5b0051d048aaf64ff0533a809e700c13f5f469f53d98143ad77e661c37fa642e" and projection["bundle_id"]==TARGET.name and projection["shutdown_successor_generation"]=="deadline_bounded_query_join_v1"
def test_collector_gate_and_sampling_contract_are_not_relaxed():
 source=(B/"qualification/mysql_distributed_live_readback_qualification_v1.py").read_text();assert "process.wait(timeout=120)" in source and '"graceful_within_seconds": 120' in source and "MINIMUM_SAMPLES = 181" in source and "consume_exact_sequence=True" in source and '"freshness_threshold_relaxed": False' in source
def test_supplement_rebound_to_exact_target_and_fresh_attempt():
 p=B/"qualification/mysql_tpch_legacy_blkio_supplement_v1.py";source=p.read_text();spec=importlib.util.spec_from_file_location("supplement",p);x=importlib.util.module_from_spec(spec);spec.loader.exec_module(x);assert x.TARGET_BUNDLE_ID==TARGET.name and x.TARGET_MANIFEST_SHA256=="386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439" and "TARGET_ATTEMPT" not in source and "TARGET_CONTAINER" not in source and 'container_name != authority.get("database_container")' in source
'''
def verify():
 m=json.loads((PARENT/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id",None);tm=json.loads((TARGET/"bundle_manifest.json").read_text());tu=dict(tm);tu.pop("bundle_id",None)
 if m.get("bundle_id")!=PARENT_ID or sf(PARENT/"bundle_manifest.json")!=PARENT_SHA or sb(canonical(u))!=PARENT_ID or fmap(PARENT)!=(m.get("files") or {}) or tm.get("bundle_id")!=TARGET_ID or sf(TARGET/"bundle_manifest.json")!=TARGET_SHA or sb(canonical(tu))!=TARGET_ID or fmap(TARGET)!=(tm.get("files") or {}) or (tm.get("component_hashes") or {}).get("sql_adapter")!=TARGET_ADAPTER:raise BuildError("parent/target closure differs")
 return m
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 parent=verify();content={n:(PARENT/n).read_bytes() for n in sorted(parent["files"])};content[ENTRY]=patch_ids(content[ENTRY]);content[SUPPLEMENT]=patch_supplement(content[SUPPLEMENT]);content[CURRENT_TEST]=patch_test(content[CURRENT_TEST]);projection=json.loads(content[PROJECTION].decode());projection["bundle_id"]=TARGET_ID;projection["bundle_manifest_sha256"]=TARGET_SHA;projection["shutdown_successor_generation"]="deadline_bounded_query_join_v1";projection["sql_adapter_sha256"]=TARGET_ADAPTER;content[PROJECTION]=canonical(projection);content["tests/test_mysql_tpch_deadline_shutdown_collector_successor_v1.py"]=TEST.encode();content["authority/build_mysql_tpch_deadline_shutdown_collector_successor_v1.py"]=HERE.read_bytes();files={n:sb(v) for n,v in sorted(content.items())};u={k:v for k,v in parent.items() if k not in ("bundle_id","files")};u.update({"schema_version":"mysql_tpch_deadline_shutdown_collector_successor_bundle_v1","status":"SEALED_LOCAL_FAIL_CLOSED_DEADLINE_TARGET_NOT_EXECUTED","parent_bundle_id":PARENT_ID,"parent_manifest_sha256":PARENT_SHA,"tpch_target_bundle_id":TARGET_ID,"tpch_target_manifest_sha256":TARGET_SHA,"tpch_sql_adapter_sha256":TARGET_ADAPTER,"shutdown_successor_generation":"deadline_bounded_query_join_v1","fresh_attempt_container_binding":"sealed_blkio_authority_plus_command_spec_v1","stale_container_name_reuse_prohibited":True,"collector_graceful_exit_gate_seconds":120,"collector_graceful_exit_gate_relaxed":False,"minimum_sample_count":181,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files});bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise BuildError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".deadline-collector-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"core_sha256":sf(b/"qualification/mysql_distributed_live_readback_qualification_v1.py"),"entry_sha256":sf(b/ENTRY),"remote_actions":0},sort_keys=True))
