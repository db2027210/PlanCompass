#!/usr/bin/env python3
"""Build Retry19 after Retry18's live-readback schema rejection (offline)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry18", ROOT / "shell/build_mysql_tpch_retry18_renderer_authority_successor_v1.py"
)
r18 = importlib.util.module_from_spec(spec); spec.loader.exec_module(r18)
h = r18.h

OLD_OUTER_ID = "1e51dbc90573fb6c80ff0465cd61c070f662c9487712088faa2a475405fcb546"
OLD_OUTER_SHA = "e85c39ffe7044efc2b85930e0e69f1a3aca6177f2327cb518d6c4256c077403b"
OLD_OUTER = ROOT / "artifacts/mysql_tpch_retry18_renderer_control_successor_v1" / OLD_OUTER_ID
OLD_TARGET_ID = r18.TARGET_ID
OLD_TARGET_SHA = r18.TARGET_SHA
OLD_TARGET = OLD_OUTER / "dependencies/bootstrap_renderer" / "88c871430544f476bb63304295e396d7bc345b49e4538cffd6d281c5a1adedca" / "target" / OLD_TARGET_ID
OLD_RENDERER_ID = "88c871430544f476bb63304295e396d7bc345b49e4538cffd6d281c5a1adedca"
OLD_RENDERER_SHA = "b03ded99ff3ec1c3d00ec61b4c2d7474072ea2a76202dcd5cf995357f8b5739c"
OLD_RENDERER = OLD_OUTER / "dependencies/bootstrap_renderer" / OLD_RENDERER_ID
SUPPORT_ID = r18.SUPPORT_ID
SUPPORT_SHA = "53bddeb7076d7e1225ca9945191f609023ea04e45909098503c4005d437a5550"
SUPPORT = OLD_OUTER / "dependencies/support" / SUPPORT_ID
OLD_MAT_ID = r18.MATERIALIZER_ID
OLD_MAT_SHA = "ec667ee4393802907b1f6d784f4b40e06bbb16b60022c28a59578234999169da"
OLD_MAT = OLD_OUTER / "dependencies/materializer" / OLD_MAT_ID
OLD_WRAP_ID = r18.WRAPPER_ID
OLD_WRAP_SHA = "98680db96509223914c02ba1d0a852326df8c3af74ddf58d52750e46e70b348e"
OLD_WRAP = OLD_OUTER / "dependencies/wrapper" / OLD_WRAP_ID
OLD_ATTEMPT = r18.ATTEMPT
OLD_UNIT = r18.UNIT
OLD_OUTPUT = r18.OUTPUT
TARGET_SCHEMA = "mysql_tpch_retry19_collector_schema_target_successor_bundle_v1"
ATTEMPT = "tpch-v2-20260804t153000p08-b235retry19collectschema"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
OUTPUT = r18.retry17.RUNTIME / ("retry19_root_oneclick_" + ATTEMPT)
TARGET_OUT = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_target_successor_v1"
RENDERER_OUT = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_renderer_successor_v1"
MAT_OUT = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_materializer_successor_v1"
WRAP_OUT = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_wrapper_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry19_collector_schema_control_successor_v1"
BUILDER_REL = "builder/build_mysql_tpch_retry19_collector_schema_successor_v1.py"


def common(old, schema, status, predecessor_id, predecessor_sha):
    value = {k:v for k,v in old.items() if k not in (
        "bundle_id","files","schema_version","status","predecessor_bundle_id",
        "predecessor_manifest_sha256")}
    value.update(schema_version=schema,status=status,
        predecessor_bundle_id=predecessor_id,
        predecessor_manifest_sha256=predecessor_sha,
        formal_scope="mysql_only",cross_engine_comparison_prohibited=True,
        postgresql_mutation_prohibited=True,formal_launch_authorized=False,
        accepted_round_authorized=False,accepted_rounds_credit=0,
        qualification_credit=0,r0_credit=0,remote_actions=0)
    return value


def build_target():
    old = h.verify(OLD_TARGET, OLD_TARGET_ID, OLD_TARGET_SHA)
    rel = "qualification/mysql_distributed_live_readback_qualification_v1.py"
    src = (OLD_TARGET/rel).read_text()
    src = h.once(src,
        '        "mysql_tpch_v23_recalibrated_successor_v3",\n    ):',
        '        "mysql_tpch_v23_recalibrated_successor_v3",\n'
        f'        "{TARGET_SCHEMA}",\n    ):',
        "Retry19 collector exact successor schema")
    content = h.copied(OLD_TARGET, old, ("builder/","tests/"))
    content[rel] = src.encode()
    failure = {
      "schema_version":"mysql_tpch_retry18_collector_schema_failure_v1",
      "status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","attempt_token":OLD_ATTEMPT,
      "outer_unit":OLD_UNIT,"outer_exec_main_pid":32820,"bootstrap_pid":32872,
      "bootstrap_starttime_ticks":262881506,"database_pid":43488,"client_pid":43778,
      "copy_pid":None,"controller_pid":None,"workload_pid":None,"sequence_started":False,
      "fresh_error":"NOT_READY: distributed bundle schema differs",
      "failure_stage":"bootstrap_collect_only_live_readback",
      "cleanup_passed":True,"post_failure_reconcile_passed":True,
      "rerun_authorized":False,"formal_scope":"mysql_only",
      "cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,
      "accepted_rounds_credit":0,"r0_credit":0,"workload_started_credit":0,
    }
    content["authority/mysql_tpch_retry18_collector_schema_failure_v1.json"] = h.canonical(failure)
    test = f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"{rel}"
q=importlib.util.spec_from_file_location("collector",P);m=importlib.util.module_from_spec(q);q.loader.exec_module(m);m.verify_bundle(B)
x=json.loads((B/"bundle_manifest.json").read_text());assert x["schema_version"]=="{TARGET_SCHEMA}" and x["live_readback_collector_schema_binding"]=="{TARGET_SCHEMA}"
assert x["query_execution_limit_seconds"]==240 and x["query_client_limit_seconds"]==250 and x["minimum_samples_unchanged"]==181 and x["sampling_hz_unchanged"]==1 and x["total_qualification_upper_bound_seconds"]==600
f=json.loads((B/"authority/mysql_tpch_retry18_collector_schema_failure_v1.json").read_text());assert f["rerun_authorized"] is False and f["controller_pid"] is None and f["workload_pid"] is None and f["accepted_rounds_credit"]==0
print("PASS_RETRY19_TARGET_COLLECTOR_SCHEMA")
'''.encode()
    content["tests/test_mysql_tpch_retry19_target_collector_schema_v1.py"] = test
    content[BUILDER_REL] = Path(__file__).read_bytes()
    unsigned = common(old,TARGET_SCHEMA,"SEALED_RETRY19_TARGET_OFFLINE_NOT_EXECUTED",OLD_TARGET_ID,OLD_TARGET_SHA)
    component_hashes = dict(unsigned.get("component_hashes") or {})
    component_hashes["collector"] = h.sb(content[rel])
    unsigned.update(live_readback_collector_schema_binding=TARGET_SCHEMA,
      component_hashes=component_hashes,
      failed_retry18_attempt=OLD_ATTEMPT,failed_retry18_rerun_authorized=False,
      query_execution_limit_seconds=240,query_client_limit_seconds=250,
      qualification_window_seconds=180,minimum_samples_unchanged=181,
      sampling_hz_unchanged=1,total_qualification_upper_bound_seconds=600,
      resource_gates_relaxed=False,synthetic_or_repeat_samples_prohibited=True)
    return h.publish(TARGET_OUT,unsigned,content,".retry19-target-")


def build_renderer(target):
    old = h.verify(OLD_RENDERER,OLD_RENDERER_ID,OLD_RENDERER_SHA)
    tsha=h.sf(target/"bundle_manifest.json"); csha=h.sf(target/"qualification/mysql_distributed_live_readback_qualification_v1.py")
    wraprel="controller/render_mysql_tpch_sql_native_percentile_bootstrap_spec_v1.py"
    src=(OLD_RENDERER/wraprel).read_text()
    replacements=(
      (f'BUNDLE_ID = "{OLD_TARGET_ID}"',f'BUNDLE_ID = "{target.name}"',"id"),
      (f'MANIFEST_SHA256 = "{OLD_TARGET_SHA}"',f'MANIFEST_SHA256 = "{tsha}"',"sha"),
      (f'"predecessor_bundle_id":\n            "{r18.TARGET_PREDECESSOR}"',f'"predecessor_bundle_id":\n            "{OLD_TARGET_ID}"',"predecessor"),
      (f'"target_schema_version":\n            "{r18.TARGET_SCHEMA}"',f'"target_schema_version":\n            "{TARGET_SCHEMA}"',"schema"),
      ('"collector_sha256":\n            "89713310afbd157963bd9efb101cc495014e2ae5722004db3e51664b3dd9d2de"',f'"collector_sha256":\n            "{csha}"',"collector"),
      ('"target_builder":\n            "builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"',f'"target_builder":\n            "{BUILDER_REL}"',"builder"),
      ('renderer.SELF_CONTAINED_VERIFIER = (\n    "builder/build_mysql_tpch_retry17_query_timeout_contract_successor_v1.py"\n)',f'renderer.SELF_CONTAINED_VERIFIER = (\n    "{BUILDER_REL}"\n)',"self builder"),
    )
    for before,after,label in replacements: src=h.once(src,before,after,"Retry19 renderer "+label)
    bindrel="b235_bootstrap_target_binding_v1.json"; binding=json.loads((OLD_RENDERER/bindrel).read_text())
    binding.update(schema_version="mysql_tpch_retry19_bootstrap_target_binding_v1",
      target_bundle_id=target.name,target_manifest_sha256=tsha,
      target_predecessor_bundle_id=OLD_TARGET_ID,target_schema_version=TARGET_SCHEMA,
      target_collector_sha256=csha,target_builder=BUILDER_REL,
      retry18_failed_attempt=OLD_ATTEMPT,retry18_rerun_authorized=False,
      accepted_rounds_credit=0)
    content=h.copied(OLD_RENDERER,old,("builder/","offline_tests/","target/"+OLD_TARGET_ID+"/"))
    content[wraprel]=src.encode();content[bindrel]=h.canonical(binding)
    for p in sorted(target.rglob("*")):
      if p.is_file():content["target/"+target.name+"/"+p.relative_to(target).as_posix()]=p.read_bytes()
    regression=f'''#!/usr/bin/env python3
import hashlib,json,pathlib,subprocess,sys,tempfile
B=pathlib.Path(sys.argv[1]).resolve();R=B/"{wraprel}";T=B/"target/{target.name}/bundle_manifest.json"
with tempfile.TemporaryDirectory(prefix="tpch-retry19-renderer-") as raw:
 d=pathlib.Path(raw);token=d/"main_agent.token";token.write_text("offline-retry19-token\\n");token.chmod(0o600);out=d/"spec.json"
 cmd=[sys.executable,"-B",str(R),"--bundle-manifest",str(T),"--mysql-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--mysql-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--client-image-reference","mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b","--client-image-id","sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172","--main-agent-token-file",str(token),"--attempt-token","tpch-v2-20260804t150000p08-b235retry19renderertest","--valid-minutes","60","--output",str(out)]
 p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30);assert p.returncode==0,(p.stdout,p.stderr)
 s=json.loads(out.read_text());assert s["bundle"]["bundle_id"]=="{target.name}" and s["formal_scope"]=="mysql_only" and s["postgresql_mutation_prohibited"] is True
print("PASS_RETRY19_REAL_RENDERER")
'''.encode()
    content["offline_tests/test_mysql_tpch_retry19_real_renderer_v1.py"]=regression
    content[BUILDER_REL]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry19_collector_schema_renderer_successor_bundle_v1","SEALED_RETRY19_RENDERER_OFFLINE_NOT_EXECUTED",OLD_RENDERER_ID,OLD_RENDERER_SHA)
    unsigned.update(target_bundle_id=target.name,target_manifest_sha256=tsha,
      target_predecessor_bundle_id=OLD_TARGET_ID,target_schema_version=TARGET_SCHEMA,
      target_collector_sha256=csha,real_renderer_invocation_regression=True,
      failed_retry18_rerun_authorized=False,query_execution_limit_seconds=240,
      query_client_limit_seconds=250,total_qualification_upper_bound_seconds=600,
      minimum_samples=181,sampling_hz=1,resource_gates_relaxed=False)
    return h.publish(RENDERER_OUT,unsigned,content,".retry19-renderer-")


def build_materializer(target):
    old=h.verify(OLD_MAT,OLD_MAT_ID,OLD_MAT_SHA);tsha=h.sf(target/"bundle_manifest.json")
    rel="materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py";src=(OLD_MAT/rel).read_text()
    src=h.once(src,f'TARGET_ID="{OLD_TARGET_ID}";TARGET_SHA="{OLD_TARGET_SHA}"',f'TARGET_ID="{target.name}";TARGET_SHA="{tsha}"',"Retry19 materializer target")
    content=h.copied(OLD_MAT,old,("builder/","tests/"));content[rel]=src.encode();content[BUILDER_REL]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry19_collector_schema_materializer_successor_bundle_v1","SEALED_RETRY19_MATERIALIZER_OFFLINE_NOT_EXECUTED",OLD_MAT_ID,OLD_MAT_SHA)
    unsigned.update(target_bundle_id=target.name,target_manifest_sha256=tsha,
      expected_sample_count=181,timeout_threshold_relaxed=False)
    return h.publish(MAT_OUT,unsigned,content,".retry19-mat-")


def build_wrapper(materializer):
    old=h.verify(OLD_WRAP,OLD_WRAP_ID,OLD_WRAP_SHA);msha=h.sf(materializer/"bundle_manifest.json")
    rel="controller/mysql_tpch_retry5_systemd219_completion_v1.py";src=(OLD_WRAP/rel).read_text()
    src=h.once(src,f'RETRY_ID="{OLD_MAT_ID}";RETRY_SHA="{OLD_MAT_SHA}"',f'RETRY_ID="{materializer.name}";RETRY_SHA="{msha}"',"Retry19 wrapper materializer")
    content=h.copied(OLD_WRAP,old,("builder/","tests/"));content[rel]=src.encode();content[BUILDER_REL]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry19_collector_schema_wrapper_successor_bundle_v1","SEALED_RETRY19_WRAPPER_OFFLINE_NOT_EXECUTED",OLD_WRAP_ID,OLD_WRAP_SHA)
    unsigned.update(retry5_materializer_bundle_id=materializer.name,
      retry5_materializer_manifest_sha256=msha)
    return h.publish(WRAP_OUT,unsigned,content,".retry19-wrap-")


def build_outer(target,renderer,materializer,wrapper):
    old=h.verify(OLD_OUTER,OLD_OUTER_ID,OLD_OUTER_SHA)
    tsha=h.sf(target/"bundle_manifest.json");rsha=h.sf(renderer/"bundle_manifest.json");msha=h.sf(materializer/"bundle_manifest.json");wsha=h.sf(wrapper/"bundle_manifest.json")
    rel=r18.retry17.CONTROLLER;src=(OLD_OUTER/rel).read_text()
    for before,after,label in (
      (f'TARGET_ID = "{OLD_TARGET_ID}"',f'TARGET_ID = "{target.name}"',"target id"),(f'TARGET_SHA = "{OLD_TARGET_SHA}"',f'TARGET_SHA = "{tsha}"',"target sha"),
      (f'RENDERER_ID = "{OLD_RENDERER_ID}"',f'RENDERER_ID = "{renderer.name}"',"renderer id"),(f'RENDERER_SHA = "{OLD_RENDERER_SHA}"',f'RENDERER_SHA = "{rsha}"',"renderer sha"),
      (f'MATERIALIZER_ID = "{OLD_MAT_ID}"',f'MATERIALIZER_ID = "{materializer.name}"',"mat id"),(f'MATERIALIZER_SHA = "{OLD_MAT_SHA}"',f'MATERIALIZER_SHA = "{msha}"',"mat sha"),
      (f'WRAPPER_ID = "{OLD_WRAP_ID}"',f'WRAPPER_ID = "{wrapper.name}"',"wrap id"),(f'WRAPPER_SHA = "{OLD_WRAP_SHA}"',f'WRAPPER_SHA = "{wsha}"',"wrap sha"),
      (f'ROOT_SYSTEMD_ATTEMPT = "{OLD_ATTEMPT}"',f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"',"attempt"),(f'ROOT_SYSTEMD_UNIT = "{OLD_UNIT}"',f'ROOT_SYSTEMD_UNIT = "{UNIT}"',"unit"),(f'ROOT_SYSTEMD_OUTPUT = Path("{OLD_OUTPUT}")',f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")',"output")):
      src=h.once(src,before,after,"Retry19 outer "+label)
    src=h.once(src,f'FAILED_RETRY17_RENDERER_ATTEMPT = "{r18.OLD_ATTEMPT}"',f'FAILED_RETRY17_RENDERER_ATTEMPT = "{r18.OLD_ATTEMPT}"\nFAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT = "{OLD_ATTEMPT}"',"Retry18 failed constant")
    anchor='''    if attempt == FAILED_RETRY17_RENDERER_ATTEMPT:
        raise OneClickError("failed Retry17 renderer-authority attempt rerun is prohibited")
    target = runtime_root / "bundles" / TARGET_ID'''
    repl='''    if attempt == FAILED_RETRY17_RENDERER_ATTEMPT:
        raise OneClickError("failed Retry17 renderer-authority attempt rerun is prohibited")
    if attempt == FAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT:
        raise OneClickError("failed Retry18 collector-schema attempt rerun is prohibited")
    target = runtime_root / "bundles" / TARGET_ID'''
    src=h.once(src,anchor,repl,"Retry18 rerun prohibition")
    skip=("builder/","tests/","dependencies/bootstrap_renderer/"+OLD_RENDERER_ID+"/","dependencies/materializer/"+OLD_MAT_ID+"/","dependencies/wrapper/"+OLD_WRAP_ID+"/")
    content=h.copied(OLD_OUTER,old,skip);content[rel]=src.encode()
    for dep,prefix in ((renderer,"dependencies/bootstrap_renderer/"),(materializer,"dependencies/materializer/"),(wrapper,"dependencies/wrapper/")):
      for p in sorted(dep.rglob("*")):
       if p.is_file():content[prefix+dep.name+"/"+p.relative_to(dep).as_posix()]=p.read_bytes()
    for f in ("launch/root_start_and_wait_retry9_systemd.sh","launch/run_retry9_root_systemd_principal.sh"):
      text=content[f].decode().replace(OLD_ATTEMPT,ATTEMPT).replace("retry18_root_oneclick_","retry19_root_oneclick_");content[f]=text.encode()
    tr="templates/node04_one_click_command.json";t=json.loads(content[tr]);t.update(schema_version="mysql_tpch_retry19_collector_schema_node04_command_v1",attempt_token=ATTEMPT,systemd_unit=UNIT,target_bundle_id=target.name,renderer_bundle_id=renderer.name,retry18_rerun_authorized=False);content[tr]=h.canonical(t)
    failrel="evidence/mysql_tpch_retry18_collector_schema_failure_v1.json";content[failrel]=h.canonical({"schema_version":"mysql_tpch_retry18_collector_schema_failure_v1","status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","attempt_token":OLD_ATTEMPT,"systemd_unit":OLD_UNIT,"outer_exec_main_pid":32820,"bootstrap_pid":32872,"bootstrap_starttime_ticks":262881506,"database_pid":43488,"client_pid":43778,"copy_pid":None,"controller_pid":None,"workload_pid":None,"sequence_started":False,"fresh_error":"NOT_READY: distributed bundle schema differs","cleanup_passed":True,"post_failure_reconcile_passed":True,"rerun_authorized":False,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"accepted_rounds_credit":0})
    testrel="tests/test_mysql_tpch_retry19_collector_schema_control_v1.py";content[testrel]=f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();R=B/"dependencies/bootstrap_renderer/{renderer.name}";T=R/"target/{target.name}";C=B/"{rel}"
subprocess.run([sys.executable,"-B",str(T/"tests/test_mysql_tpch_retry19_target_collector_schema_v1.py"),str(T)],check=True)
subprocess.run([sys.executable,"-B",str(R/"offline_tests/test_mysql_tpch_retry19_real_renderer_v1.py"),str(R)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package();assert p["renderer"].name=="{renderer.name}" and o.TARGET_ID=="{target.name}" and o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and o.FAILED_RETRY18_COLLECTOR_SCHEMA_ATTEMPT=="{OLD_ATTEMPT}"
m=json.loads((B/"bundle_manifest.json").read_text());assert m["minimum_samples_unchanged"]==181 and m["sampling_hz_unchanged"]==1 and m["total_qualification_upper_bound_seconds"]==600 and m["resource_gates_relaxed"] is False
print("PASS_RETRY19_COLLECTOR_SCHEMA_CONTROL")
'''.encode();content[BUILDER_REL]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry19_collector_schema_control_successor_bundle_v1","SEALED_LOCAL_RETRY19_COLLECTOR_SCHEMA_NOT_EXECUTED",OLD_OUTER_ID,OLD_OUTER_SHA)
    unsigned.update(target_bundle_id=target.name,target_manifest_sha256=tsha,renderer_bundle_id=renderer.name,renderer_manifest_sha256=rsha,support_bundle_id=SUPPORT_ID,support_manifest_sha256=SUPPORT_SHA,materializer_bundle_id=materializer.name,materializer_manifest_sha256=msha,wrapper_bundle_id=wrapper.name,wrapper_manifest_sha256=wsha,failed_retry18_attempt=OLD_ATTEMPT,failed_retry18_rerun_authorized=False,new_attempt_token=ATTEMPT,root_systemd_unit=UNIT,query_execution_limit_seconds=240,query_client_limit_seconds=250,qualification_window_seconds=180,minimum_samples_unchanged=181,sampling_hz_unchanged=1,total_qualification_upper_bound_seconds=600,resource_gates_relaxed=False,synthetic_or_repeat_samples_prohibited=True,failure_propagation_required=True)
    return h.publish(OUT,unsigned,content,".retry19-outer-")


def main():
    target=build_target();renderer=build_renderer(target);materializer=build_materializer(target);wrapper=build_wrapper(materializer);outer=build_outer(target,renderer,materializer,wrapper)
    print(json.dumps({"bundle":str(outer),"bundle_id":outer.name,"manifest_sha256":h.sf(outer/"bundle_manifest.json"),"target_bundle_id":target.name,"target_manifest_sha256":h.sf(target/"bundle_manifest.json"),"renderer_bundle_id":renderer.name,"renderer_manifest_sha256":h.sf(renderer/"bundle_manifest.json"),"materializer_bundle_id":materializer.name,"materializer_manifest_sha256":h.sf(materializer/"bundle_manifest.json"),"wrapper_bundle_id":wrapper.name,"wrapper_manifest_sha256":h.sf(wrapper/"bundle_manifest.json"),"attempt_token":ATTEMPT,"systemd_unit":UNIT},sort_keys=True))


if __name__=="__main__":
 try:main()
 except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:raise SystemExit("NOT_READY: "+str(exc))
