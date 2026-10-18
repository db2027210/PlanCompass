#!/usr/bin/env python3
"""Build the fresh-attempt v13 controller using the recreated-epoch collector."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT=Path(__file__).resolve().parents[1]
V12_CONTROL=ROOT/"shell/mysql_tpcc_qualification_fresh_r0_v12.py"
V12_LIB=ROOT/"artifacts/mysql_tpcc_qualification_fresh_r0_v12/05e3001e4da4fd553e914d720051754943afb3d20daf1b64e83ca6ac89340803/lib/mysql_tpcc_inventory_controller_v1.py"
TYPED_ID="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
TYPED=ROOT/"artifacts/mysql_tpcc_typed_collector_portable_v2"/TYPED_ID
COLLECTOR_ID="ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec"
COLLECTOR_MANIFEST_SHA="a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"
OUT=ROOT/"artifacts/mysql_tpcc_qualification_fresh_r0_v13"
PINNED="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
OUTPUT="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v13-qualification-fresh-r0"


def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def digest(v):return hashlib.sha256(v).hexdigest()
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(root,0o555)


TEST=r'''import ast,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];M=B/"bundle_manifest.json";C=B/"control/mysql_tpcc_qualification_fresh_r0_v13.py";L=B/"lib/mysql_tpcc_inventory_controller_v1.py"
def can(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_content_address_scope_zero_credit():m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(can(u)).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_fresh_v13_not_v12_rerun():s=C.read_text();ast.parse(s);assert 'tag = "v13-"' in s and "mysql_tpcc_qualification_fresh_r0_plan_v13" in s and "v12-20260804T081044Z-efe07bc17f49" not in s
def test_new_collector_exact():s=C.read_text();assert 'COLLECTOR_ID = "ba98dab53beabbf2fefee60c0fe6795e6c2271524fdab335c01679b7a98693ec"' in s and 'COLLECTOR_MANIFEST_SHA = "a38915982e408c32f6e94ad4e88fbbdd323030190b3b525e6a570c9e2dbfd127"' in s
def test_real_preflight_is_mandatory_before_workload():s=C.read_text();assert "def run_collector_preflight" in s and s.index("run_collector_preflight(plan, run_root, collector)")<s.index('run_stage(plan, "qualification"')
def test_new_service_epoch_through_controller():
 s=C.read_text()
 for x in ("b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001","fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948","DB_PID = 100769","DB_START = 268728753"):assert x in s
def test_exact_order_and_no_credit():s=C.read_text();assert "DURATION = 180" in s and "SAMPLES = 181" in s and s.index('run_stage(plan, "qualification"')<s.index('run_stage(plan, "fresh_r0"') and '"accepted_rounds_credit": 0' in s
def test_inherited_sample_scope_fix_retained():s=L.read_text();ast.parse(s);assert "pg=sample.get" in s and "pg=value.get" not in s
def test_v12_failure_is_zero_credit_and_prohibited():m=json.loads(M.read_text());f=json.loads((B/"authority/v12_failure.json").read_text());assert m["failed_v12_rerun_prohibited"] is True and f["accepted_rounds_credit"]==0 and f["rerun_prohibited"] is True
def test_runbook_is_new_root_and_main_agent_only():r=json.loads((B/"runbook/one_click.json").read_text());assert "v13-qualification-fresh-r0" in r["argv"][-1] and r["main_agent_execution_required"] is True and r["build_executes_workload"] is False
'''


def main():
 source=V12_CONTROL.read_text()
 if source.count('COLLECTOR_ID = "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"')!=1 or source.count('COLLECTOR_MANIFEST_SHA = "842fbef539b8c2f42805ed301a41b3f75900b927074b4f9c625a406109dbe4bc"')!=1:raise RuntimeError("v12 collector constants differ")
 source=source.replace('COLLECTOR_ID = "f5a7a688f1c981b6c92e6dae0cf6aac580d0326f7f4d1f46c7493af993bc8a23"','COLLECTOR_ID = "'+COLLECTOR_ID+'"').replace('COLLECTOR_MANIFEST_SHA = "842fbef539b8c2f42805ed301a41b3f75900b927074b4f9c625a406109dbe4bc"','COLLECTOR_MANIFEST_SHA = "'+COLLECTOR_MANIFEST_SHA+'"')
 source=source.replace("v12","v13").replace("V12","V13")
 preflight='''\n\ndef run_collector_preflight(plan, run_root, collector):\n    args = plan["qualification_args"]\n    def value(name):\n        index = args.index(name)\n        return args[index + 1]\n    entry = collector / "qualification/mysql_tpcc_recreated_authority_preflight_v13.py"\n    stdout_path = run_root / "collector_preflight.stdout"\n    stderr_path = run_root / "collector_preflight.stderr"\n    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:\n        result = subprocess.run([str(PINNED), "-B", str(entry), "--bundle", value("--bundle"), "--container", value("--container"), "--defaults-file", value("--mysql-defaults-file")], stdout=stdout, stderr=stderr, timeout=120)\n    if result.returncode:\n        raise NotReady("recreated-authority collector preflight failed rc=" + str(result.returncode))\n    try:\n        evidence = json.loads(stdout_path.read_text())\n    except (OSError, json.JSONDecodeError) as exc:\n        raise NotReady("collector preflight output differs") from exc\n    if (evidence.get("status") != "PASS_READ_ONLY_RECREATED_AUTHORITY_PREFLIGHT"\n            or evidence.get("collector_bundle_id") != COLLECTOR_ID\n            or evidence.get("database_container_id") != DB_ID\n            or int(evidence.get("database_pid") or 0) != DB_PID\n            or int(evidence.get("database_process_start_ticks") or 0) != DB_START\n            or evidence.get("adopted_authority_sha256") != ADOPTED_SHA\n            or evidence.get("blkio_authority_sha256") != "b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8"\n            or evidence.get("postgresql_mutation_performed") is not False\n            or evidence.get("workload_started") is not False\n            or evidence.get("accepted_rounds_credit") != 0):\n        raise NotReady("collector preflight evidence differs")\n    completion = run_root / "collector_preflight.completion.json"\n    write_sealed(completion, {"schema_version": "mysql_tpcc_v13_collector_preflight_completion", **evidence})\n    return completion\n'''
 marker='\ndef run_stage(plan, phase, run_root, collector, collector_manifest):\n'
 if source.count(marker)!=1:raise RuntimeError("v13 preflight insertion marker differs")
 source=source.replace(marker,preflight+marker)
 execute_old='''    collector, _, collector_manifest = resolve(COLLECTOR_ID, COLLECTOR_MANIFEST_SHA, "dynamic collector")\n    live()\n    run_root = Path(plan["run_root"])\n    run_root.mkdir(parents=True, mode=0o700)\n    try:\n        qualification_output, _, qualification_dynamic, qualification_pids = run_stage(plan, "qualification", run_root, collector, collector_manifest)'''
 execute_new='''    collector, _, collector_manifest = resolve(COLLECTOR_ID, COLLECTOR_MANIFEST_SHA, "dynamic collector")\n    run_root = Path(plan["run_root"])\n    run_root.mkdir(parents=True, mode=0o700)\n    try:\n        live()\n        collector_preflight_path = run_collector_preflight(plan, run_root, collector)\n        qualification_output, _, qualification_dynamic, qualification_pids = run_stage(plan, "qualification", run_root, collector, collector_manifest)'''
 if source.count(execute_old)!=1:raise RuntimeError("v13 execute preflight marker differs")
 source=source.replace(execute_old,execute_new)
 source=source.replace('"evidence_sha256": sha(qualification_output), "dynamic_gate"','"collector_preflight_sha256": sha(collector_preflight_path), "evidence_sha256": sha(qualification_output), "dynamic_gate"')
 failure={"schema_version":"mysql_tpcc_v12_failed_attempt_invalidation_v1","failed_output_root":"/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v12-qualification-fresh-r0/v12-20260804T081044Z-efe07bc17f49","status":"FAIL_CLOSED_EFFECTIVE_BLKIO_OLD_AUTHORITY","root_cause":"raw collector selected generic blkio_throttle_authority.json bound to archived database epoch","rerun_prohibited":True,"reusable":False,"accepted_rounds_credit":0,"postgresql_mutation_performed":False}
 runbook={"schema_version":"mysql_tpcc_qualification_fresh_r0_one_click_runbook_v13","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"main_agent_execution_required":True,"build_executes_workload":False,"collector_preflight_required":True,"collector_bundle_id":COLLECTOR_ID,"execution_order":["recreated_authority_read_only_preflight","qualification_exact_180s_181_samples","fresh_r0_exact_180s_181_samples"],"argv":[PINNED,"-B","[THIS_PUBLISHED_BUNDLE]/control/mysql_tpcc_qualification_fresh_r0_v13.py","one-click","--output-root",OUTPUT],"accepted_rounds_credit":0}
 binding={"schema_version":"mysql_tpcc_recreated_service_binding_v13","adopted_service_sha256":"b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001","blkio_authority_sha256":"b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8","database_container_id":"fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948","database_pid":100769,"database_process_start_ticks":268728753,"collector_bundle_id":COLLECTOR_ID,"qualification_before_fresh_r0":True,"accepted_rounds_credit":0,"postgresql_mutation_prohibited":True}
 contents={"control/mysql_tpcc_qualification_fresh_r0_v13.py":source.encode(),"lib/mysql_tpcc_inventory_controller_v1.py":V12_LIB.read_bytes(),"authority/recreated_service_binding_v13.json":canonical(binding),"authority/v12_failure.json":canonical(failure),"runbook/one_click.json":canonical(runbook),"tests/test_mysql_tpcc_qualification_fresh_r0_v13.py":TEST.encode(),"builder/build_mysql_tpcc_qualification_fresh_r0_v13.py":Path(__file__).read_bytes()}
 tm=json.loads((TYPED/"bundle_manifest.json").read_text())
 if tm.get("bundle_id")!=TYPED_ID:raise RuntimeError("typed collector differs")
 for p in TYPED.iterdir():
  if p.is_file():contents["typed_collector/"+p.name]=p.read_bytes()
 files={r:digest(p) for r,p in sorted(contents.items())}
 unsigned={"schema_version":"mysql_tpcc_qualification_fresh_r0_bundle_v13","status":"SEALED_NOT_PUBLISHED_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"collector_bundle_id":COLLECTOR_ID,"collector_manifest_sha256":COLLECTOR_MANIFEST_SHA,"recreated_adopted_authority_sha256":"b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001","recreated_blkio_authority_sha256":"b823a7c61f5d11dfa50a13f9fa1ea268b70410004a57b8449bc1e442bc1a11e8","failed_v12_rerun_prohibited":True,"dynamic_window_seconds":180,"exact_sample_count":181,"qualification_before_fresh_r0":True,"formal_outer_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_performed":0,"data_plane_actions_performed":0,"files":files}
 identity=digest(canonical(unsigned));manifest=dict(unsigned,bundle_id=identity);raw=canonical(manifest);OUT.mkdir(parents=True,exist_ok=True);target=OUT/identity
 if target.exists():
  if (target/"bundle_manifest.json").read_bytes()!=raw:raise RuntimeError("existing manifest differs")
  for r,p in contents.items():
   if (target/r).read_bytes()!=p:raise RuntimeError("existing closure differs: "+r)
 else:
  stage=Path(tempfile.mkdtemp(prefix=".tpcc-v13-controller-",dir=OUT))
  try:
   for r,p in contents.items():q=stage/r;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(p)
   (stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(digest(raw)+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,target)
  finally:
   if stage.exists():shutil.rmtree(stage)
 print(identity);print(digest(raw));print(target)


if __name__=="__main__":main()
