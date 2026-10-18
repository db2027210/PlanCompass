#!/usr/bin/env python3
"""Build Retry22 by binding the real Retry21 target in the runtime support."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("r21",ROOT/"shell/build_mysql_tpch_retry21_collector_schema_successor_v1.py")
r21=importlib.util.module_from_spec(spec);spec.loader.exec_module(r21);h=r21.h
OLD_SUPPORT_ID="ab465b86fb5b5b1906d512e8864c00e64fe2413a451c38e9649b4ef1116ad2d7"
OLD_SUPPORT_SHA="53bddeb7076d7e1225ca9945191f609023ea04e45909098503c4005d437a5550"
OLD_OUTER_ID="1ccc7f6bdffad9cd0a1945fb6e2cc67ab629aa480f0be64df1d734839501b549"
OLD_OUTER_SHA="35fdd01a4000260530560dd5dad1a8d409b37246ccb3c7cf5fa68377e3b40f56"
OLD_SUPPORT=ROOT/"artifacts/mysql_tpch_retry17_query_timeout_support_successor_v1"/OLD_SUPPORT_ID
OLD_OUTER=ROOT/"artifacts/mysql_tpch_retry21_collector_schema_control_successor_v1"/OLD_OUTER_ID
TARGET=ROOT/"artifacts/mysql_tpch_retry21_collector_schema_target_successor_v1"/"0179951ba054c4f594900ff19b60e4dced5496f740eb036689b1630010bb6aaa"
TARGET_ID=TARGET.name
TARGET_SHA="cda847c745c263c6c198bdd6122c177ed5b448a113ec44abba23975b7eb8f61b"
TARGET_SCHEMA="mysql_tpch_retry21_collector_schema_successor_bundle_v1"
TARGET_COLLECTOR="e510990bbbc698eaccc28691f0d646efaadcaaff0eac8eaabfc453c9dad90568"
OLD_ATTEMPT="tpch-v2-20260804t161000p08-b235retry21collectorschema"
ATTEMPT="tpch-v2-20260804t163000p08-b235retry22supportseal"
OLD_UNIT="ra-mysql-tpch-oneclick-root-"+OLD_ATTEMPT+".service";UNIT="ra-mysql-tpch-oneclick-root-"+ATTEMPT+".service"
RUNTIME=Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OLD_OUTPUT=RUNTIME/("retry21_root_oneclick_"+OLD_ATTEMPT);OUTPUT=RUNTIME/("retry22_root_oneclick_"+ATTEMPT)
BUILDER_REL="builder/build_mysql_tpch_retry22_current_target_support_successor_v1.py"
SUPPORT_OUT=ROOT/"artifacts/mysql_tpch_retry22_current_target_support_successor_v1"
OUTER_OUT=ROOT/"artifacts/mysql_tpch_retry22_current_target_support_control_successor_v1"


def common(old,schema,status,predecessor_id,predecessor_sha):
 v={k:x for k,x in old.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256")}
 v.update(schema_version=schema,status=status,predecessor_bundle_id=predecessor_id,predecessor_manifest_sha256=predecessor_sha,formal_scope="mysql_only",cross_engine_comparison_prohibited=True,postgresql_mutation_prohibited=True,formal_launch_authorized=False,accepted_round_authorized=False,accepted_rounds_credit=0,qualification_credit=0,r0_credit=0,remote_actions=0)
 return v


def build_support():
 old=h.verify(OLD_SUPPORT,OLD_SUPPORT_ID,OLD_SUPPORT_SHA);rel="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";s=(OLD_SUPPORT/rel).read_text()
 for before,after,count,label in (
  ('"bundle_id": "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"','"bundle_id": "'+TARGET_ID+'"',1,"target id"),
  ('"manifest_sha256": "386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439"','"manifest_sha256": "'+TARGET_SHA+'"',1,"manifest sha"),
  ('"mysql_tpch_v23_recalibrated_successor_v3"','"'+TARGET_SCHEMA+'"',2,"schema"),
  ('"89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"','"'+TARGET_COLLECTOR+'"',1,"collector component"),
 ):
  if s.count(before)!=count:raise RuntimeError("Retry22 support marker differs: "+label)
  s=s.replace(before,after)
 content=h.copied(OLD_SUPPORT,old,("builder/","tests/"));content[rel]=s.encode();content["authority/mysql_tpch_retry21_current_target_support_failure_v1.json"]=h.canonical({"schema_version":"mysql_tpch_retry21_current_target_support_failure_v1","status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","attempt_token":OLD_ATTEMPT,"fresh_error":"current target seal differs","expected":{"bundle_id":"b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47","manifest_sha256":"386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439","schema_version":"mysql_tpch_v23_recalibrated_successor_v3","collector":"89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"},"actual":{"bundle_id":TARGET_ID,"manifest_sha256":TARGET_SHA,"schema_version":TARGET_SCHEMA,"collector":TARGET_COLLECTOR},"bootstrap_passed":True,"workload_pid":None,"post_failure_reconcile_passed":True,"rerun_authorized":False,"accepted_rounds_credit":0,"postgresql_mutation_prohibited":True})
 content["tests/test_mysql_tpch_retry22_current_target_support_v1.py"]=f'''import ast,json,pathlib
B=pathlib.Path(__file__).resolve().parents[1];E=B/"{rel}"
def test_exact_target_fields():
 s=E.read_text();ast.parse(s)
 for x in ("{TARGET_ID}","{TARGET_SHA}","{TARGET_SCHEMA}","{TARGET_COLLECTOR}"):assert x in s
 for x in ("b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47","386f59b23f20eb7be59665c42c44fe2453512bf2112d850e12df6b398a16f439","89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"):assert x not in s
def test_failure_zero_credit():f=json.loads((B/"authority/mysql_tpch_retry21_current_target_support_failure_v1.json").read_text());assert f["workload_pid"] is None and f["rerun_authorized"] is False and f["accepted_rounds_credit"]==0
'''.encode();content[BUILDER_REL]=Path(__file__).read_bytes()
 unsigned=common(old,"mysql_tpch_retry22_current_target_support_successor_bundle_v1","SEALED_RETRY22_SUPPORT_NOT_EXECUTED",OLD_SUPPORT_ID,OLD_SUPPORT_SHA);unsigned.update(target_bundle_id=TARGET_ID,target_manifest_sha256=TARGET_SHA,target_schema_version=TARGET_SCHEMA,target_collector_sha256=TARGET_COLLECTOR,failed_retry21_attempt=OLD_ATTEMPT,failed_retry21_rerun_authorized=False,current_target_fields_changed=["bundle_id","manifest_sha256","schema_version","component_hashes.collector"],resource_gates_relaxed=False,minimum_samples_unchanged=181,sampling_hz_unchanged=1)
 return h.publish(SUPPORT_OUT,unsigned,content,".retry22-support-")


def build_outer(support):
 old=h.verify(OLD_OUTER,OLD_OUTER_ID,OLD_OUTER_SHA);support_sha=h.sf(support/"bundle_manifest.json");rel="controller/mysql_tpch_retry9_one_click_v1.py";s=(OLD_OUTER/rel).read_text()
 for before,after,label in ((f'SUPPORT_ID = "{OLD_SUPPORT_ID}"',f'SUPPORT_ID = "{support.name}"',"support id"),(f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"',f'SUPPORT_SHA = "{support_sha}"',"support sha"),(f'ROOT_SYSTEMD_ATTEMPT = "{OLD_ATTEMPT}"',f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"',"attempt"),(f'ROOT_SYSTEMD_UNIT = "{OLD_UNIT}"',f'ROOT_SYSTEMD_UNIT = "{UNIT}"',"unit"),(f'ROOT_SYSTEMD_OUTPUT = Path("{OLD_OUTPUT}")',f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")',"output")):
  s=h.once(s,before,after,"Retry22 outer "+label)
 s=h.once(s,f'FAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT = "{r21.OLD_ATTEMPT}"',f'FAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT = "{r21.OLD_ATTEMPT}"\nFAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT = "{OLD_ATTEMPT}"',"Retry22 failed constant")
 s=h.once(s,'    if attempt == FAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT:\n        raise OneClickError("failed Retry20 prepublication collector-schema attempt rerun is prohibited")\n    target = runtime_root / "bundles" / TARGET_ID','    if attempt == FAILED_RETRY20_COLLECTOR_SCHEMA_ATTEMPT:\n        raise OneClickError("failed Retry20 prepublication collector-schema attempt rerun is prohibited")\n    if attempt == FAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT:\n        raise OneClickError("failed Retry21 current-target-support attempt rerun is prohibited")\n    target = runtime_root / "bundles" / TARGET_ID',"Retry21 rerun prohibition")
 skip=("builder/","tests/","dependencies/support/"+OLD_SUPPORT_ID+"/");content=h.copied(OLD_OUTER,old,skip);content[rel]=s.encode()
 for p in sorted(support.rglob("*")):
  if p.is_file():content["dependencies/support/"+support.name+"/"+p.relative_to(support).as_posix()]=p.read_bytes()
 for launch_rel in ("launch/root_start_and_wait_retry9_systemd.sh","launch/run_retry9_root_systemd_principal.sh"):
  content[launch_rel]=content[launch_rel].decode().replace(OLD_ATTEMPT,ATTEMPT).replace("retry21_root_oneclick_","retry22_root_oneclick_").encode()
 tr="templates/node04_one_click_command.json";template=json.loads(content[tr]);template.update(schema_version="mysql_tpch_retry22_current_target_support_node04_command_v1",attempt_token=ATTEMPT,systemd_unit=UNIT,support_bundle_id=support.name,support_closure_bundle_id=support.name,retry21_rerun_authorized=False);content[tr]=h.canonical(template)
 content["evidence/mysql_tpch_retry21_current_target_support_failure_v1.json"]=(support/"authority/mysql_tpch_retry21_current_target_support_failure_v1.json").read_bytes()
 content["tests/test_mysql_tpch_retry22_current_target_support_control_v1.py"]=f'''import importlib.util,json,pathlib
B=pathlib.Path(__file__).resolve().parents[1];C=B/"{rel}"
def test_package_and_new_attempt():
 q=importlib.util.spec_from_file_location("outer22",C);m=importlib.util.module_from_spec(q);q.loader.exec_module(m);p=m.verify_package();assert p["support"].name=="{support.name}" and m.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and m.FAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT=="{OLD_ATTEMPT}"
def test_zero_credit():m=json.loads((B/"bundle_manifest.json").read_text());assert m["accepted_rounds_credit"]==0 and m["failed_retry21_rerun_authorized"] is False
def test_template_binds_new_support_once():t=json.loads((B/"templates/node04_one_click_command.json").read_text());assert t["support_bundle_id"]==t["support_closure_bundle_id"]=="{support.name}"
'''.encode();content[BUILDER_REL]=Path(__file__).read_bytes()
 unsigned=common(old,"mysql_tpch_retry22_current_target_support_control_successor_bundle_v1","SEALED_LOCAL_RETRY22_NOT_EXECUTED",OLD_OUTER_ID,OLD_OUTER_SHA);unsigned.update(target_bundle_id=TARGET_ID,target_manifest_sha256=TARGET_SHA,support_bundle_id=support.name,support_manifest_sha256=support_sha,failed_retry21_attempt=OLD_ATTEMPT,failed_retry21_rerun_authorized=False,new_attempt_token=ATTEMPT,root_systemd_unit=UNIT,current_target_support_fields_fixed=["bundle_id","manifest_sha256","schema_version","component_hashes.collector"],minimum_samples_unchanged=181,sampling_hz_unchanged=1,total_qualification_upper_bound_seconds=600,resource_gates_relaxed=False,synthetic_or_repeat_samples_prohibited=True,failure_propagation_required=True)
 return h.publish(OUTER_OUT,unsigned,content,".retry22-outer-")


def main():
 h.verify(TARGET,TARGET_ID,TARGET_SHA);support=build_support();outer=build_outer(support);print(json.dumps({"bundle":str(outer),"bundle_id":outer.name,"manifest_sha256":h.sf(outer/"bundle_manifest.json"),"support_bundle_id":support.name,"support_manifest_sha256":h.sf(support/"bundle_manifest.json"),"attempt_token":ATTEMPT,"systemd_unit":UNIT},sort_keys=True))


if __name__=="__main__":
 try:main()
 except (OSError,ValueError,KeyError,TypeError,RuntimeError) as e:raise SystemExit("NOT_READY: "+str(e))
