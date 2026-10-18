#!/usr/bin/env python3
"""Build Retry14 with exact ``python -B script`` control-plane identities."""
from __future__ import annotations
import importlib.util,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
q=importlib.util.spec_from_file_location("r13",ROOT/"shell/build_mysql_tpch_retry13_solo_collector_identity_successor_v1.py")
r=importlib.util.module_from_spec(q);q.loader.exec_module(r);h=r.h
OLD_OUTER_ID="88e3a519f605b856089b413f3eee485ab261c639ef42b96da884cf450e4f241e";OLD_OUTER_SHA="f57fee0160f3f95278e9931c2cd697f691c1ec2a841075a37d6076fc22c8f157"
BASE=ROOT/"artifacts/mysql_tpch_retry13_solo_collector_identity_successor_v1"/OLD_OUTER_ID
OLD_SUPPORT_ID="718ac9da9e826211f23b6468c90a39e88a0cc0d9e6f6e393caee84fed6a18e00";OLD_SUPPORT_SHA="cfd74a0e38e1f63c9db0d27caa3fb30b13d31cf16584c69c599cff64f1fc038a";OLD_ENTRY_SHA="066eca5b4947d5e482be1aadac33c651f7cf3760cb818fb64fe6de6d229efcd6"
OLD_MAT_ID="5c5ccd5ed8f97f767c9b5dae284d779fe033c7d2aa695959623eee3d2d06755f";OLD_MAT_SHA="48717c297b1cc4932865f3dc10309e6fc967edad7097752644e23ae1e13e2751"
OLD_WRAP_ID="b61dcb4771686f20003a2fcc47ca8116ec61b91eee81371950c8e468a3653f2f";OLD_WRAP_SHA="9b93e502f1d06079c485becb70c97e0f37f98dbfc7e449469dc01a48a256c82d"
SUPPORT_OLD=BASE/"dependencies/support"/OLD_SUPPORT_ID;MAT_OLD=BASE/"dependencies/materializer"/OLD_MAT_ID;WRAP_OLD=BASE/"dependencies/wrapper"/OLD_WRAP_ID
CORE_SHA=r.CORE_SHA;ENTRY_REL=r.ENTRY_REL;CORE_REL=r.CORE_REL;CONTROLLER=r.CONTROLLER;RUNTIME=r.RUNTIME
FAILED="tpch-v2-20260803t214500p08-b235retry13solo7c8f1";ATTEMPT="tpch-v2-20260803t220000p08-b235retry14argv2d6e9";UNIT="ra-mysql-tpch-oneclick-root-"+ATTEMPT+".service";OUTPUT=RUNTIME/("retry14_root_oneclick_"+ATTEMPT)
FAILURE="evidence/mysql_tpch_retry13_python_b_argv_identity_failure_v1.json"
SUPPORT_OUT=ROOT/"artifacts/mysql_tpch_retry14_python_b_support_successor_v1";MAT_OUT=ROOT/"artifacts/mysql_tpch_retry14_python_b_materializer_successor_v1";WRAP_OUT=ROOT/"artifacts/mysql_tpch_retry14_python_b_wrapper_successor_v1";OUT=ROOT/"artifacts/mysql_tpch_retry14_python_b_argv_successor_v1"

def common(old,schema,status,pid,psha):
 v={k:x for k,x in old.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256")};v.update(schema_version=schema,status=status,predecessor_bundle_id=pid,predecessor_manifest_sha256=psha,formal_scope="mysql_only",cross_engine_comparison_prohibited=True,postgresql_mutation_prohibited=True,formal_launch_authorized=False,accepted_round_authorized=False,accepted_rounds_credit=0,remote_actions=0);return v

def build_support():
 old=h.verify(SUPPORT_OLD,OLD_SUPPORT_ID,OLD_SUPPORT_SHA);source=(SUPPORT_OLD/ENTRY_REL).read_text()
 source=h.once(source,'if (len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve()\n            or Path(argv[1]).resolve() != Path(entry)\n            or argv.count("--qualification-paired") != 1):','if (len(argv) < 4 or Path(argv[0]).resolve() != Path(sys.executable).resolve()\n            or argv[1] != "-B" or Path(argv[2]).resolve() != Path(entry)\n            or argv.count("--qualification-paired") != 1):',"paired python-B identity")
 source=h.once(source,'if (len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve()\n            or Path(argv[1]).resolve() != Path(entry)\n            or argv.count("--qualification-solo") != 1','if (len(argv) < 4 or Path(argv[0]).resolve() != Path(sys.executable).resolve()\n            or argv[1] != "-B" or Path(argv[2]).resolve() != Path(entry)\n            or argv.count("--qualification-solo") != 1',"solo python-B identity")
 source=h.once(source,'if len(argv) < 2 or Path(argv[1]).resolve() != Path(__file__).resolve() or "--qualification-paired" not in argv:','if len(argv) < 3 or argv[1] != "-B" or Path(argv[2]).resolve() != Path(__file__).resolve() or "--qualification-paired" not in argv:',"peer python-B identity")
 source=h.once(source,'if (len(argv) >= 3\n                and Path(argv[1]).name\n                == "mysql_tpch_retry5_systemd219_completion_v1.py"','if (len(argv) >= 4 and argv[1] == "-B"\n                and Path(argv[2]).name\n                == "mysql_tpch_retry5_systemd219_completion_v1.py"',"controller python-B identity")
 content=h.copied(SUPPORT_OLD,old,("builder/","tests/"));content[ENTRY_REL]=source.encode();content["builder/build_mysql_tpch_retry14_python_b_argv_successor_v1.py"]=Path(__file__).read_bytes()
 content["tests/test_mysql_tpch_retry14_python_b_argv_identity_v1.py"]=f'''#!/usr/bin/env python3
import importlib.util,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"{ENTRY_REL}";q=importlib.util.spec_from_file_location("c",P);c=importlib.util.module_from_spec(q);q.loader.exec_module(c)
for mode in ("--qualification-solo","--qualification-paired"):
 argv=[sys.executable,"-B",str(P),mode,"--command-spec","/absent-command","--main-agent-authority","/absent-authority","--main-agent-token-file","/absent-token"]
 try:c._sealed_collector_command(argv)
 except Exception as e:assert "executable identity differs" not in str(e),str(e)
 else:raise AssertionError("absent seals passed")
bad=[sys.executable,str(P),"--qualification-solo"]
try:c._sealed_collector_command(bad)
except Exception as e:assert "executable identity differs" in str(e)
else:raise AssertionError("missing -B passed")
s=P.read_text();assert 'argv[1] != "-B"' in s and 'Path(argv[2]).name' in s
print("PASS_RETRY14_EXACT_PYTHON_B_ARGV_IDENTITY")
'''.encode()
 unsigned=common(old,"mysql_tpch_retry14_python_b_support_successor_bundle_v1","SEALED_EXACT_PYTHON_B_ARGV_SUPPORT_NOT_EXECUTED",OLD_SUPPORT_ID,OLD_SUPPORT_SHA);unsigned.update(python_flag_position=1,script_position=2,python_no_bytecode_flag="-B",solo_collector_identity_supported=True,paired_collector_identity_supported=True,controller_ancestor_identity_supported=True,failed_retry13_attempt_token=FAILED,failed_retry13_rerun_prohibited=True)
 return h.publish(SUPPORT_OUT,unsigned,content,".retry14-support-")

def build_materializer(support):
 old=h.verify(MAT_OLD,OLD_MAT_ID,OLD_MAT_SHA);ss=h.sf(support/"bundle_manifest.json");es=h.sf(support/ENTRY_REL);rel="materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py";source=(MAT_OLD/rel).read_text();source=h.once(source,f'SUPPORT_ID="{OLD_SUPPORT_ID}";SUPPORT_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',f'SUPPORT_ID="{support.name}";SUPPORT_SHA="{ss}";ENTRY_SHA="{es}";CORE_SHA="{CORE_SHA}"',"materializer support")
 content=h.copied(MAT_OLD,old,("builder/","tests/"));content[rel]=source.encode();content["builder/build_mysql_tpch_retry14_python_b_argv_successor_v1.py"]=Path(__file__).read_bytes();u=common(old,"mysql_tpch_retry14_python_b_materializer_successor_bundle_v1","SEALED_RETRY14_MATERIALIZER_NOT_EXECUTED",OLD_MAT_ID,OLD_MAT_SHA);u.update(support_bundle_id=support.name,support_manifest_sha256=ss,support_entry_sha256=es,support_core_sha256=CORE_SHA);return h.publish(MAT_OUT,u,content,".retry14-mat-")

def build_wrapper(support,materializer):
 old=h.verify(WRAP_OLD,OLD_WRAP_ID,OLD_WRAP_SHA);ss=h.sf(support/"bundle_manifest.json");es=h.sf(support/ENTRY_REL);ms=h.sf(materializer/"bundle_manifest.json");rel="controller/mysql_tpch_retry5_systemd219_completion_v1.py";source=(WRAP_OLD/rel).read_text();source=h.once(source,f'RETRY_ID="{OLD_MAT_ID}";RETRY_SHA="{OLD_MAT_SHA}"',f'RETRY_ID="{materializer.name}";RETRY_SHA="{ms}"',"wrapper materializer");source=h.once(source,f'COLLECTOR_ID="{OLD_SUPPORT_ID}";COLLECTOR_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',f'COLLECTOR_ID="{support.name}";COLLECTOR_SHA="{ss}";ENTRY_SHA="{es}";CORE_SHA="{CORE_SHA}"',"wrapper support")
 content=h.copied(WRAP_OLD,old,("builder/","tests/"));content[rel]=source.encode();content["builder/build_mysql_tpch_retry14_python_b_argv_successor_v1.py"]=Path(__file__).read_bytes();u=common(old,"mysql_tpch_retry14_python_b_wrapper_successor_bundle_v1","SEALED_RETRY14_WRAPPER_NOT_EXECUTED",OLD_WRAP_ID,OLD_WRAP_SHA);u.update(retry5_materializer_bundle_id=materializer.name,retry5_materializer_manifest_sha256=ms,collector_bundle_id=support.name,collector_manifest_sha256=ss,collector_entry_sha256=es,collector_core_sha256=CORE_SHA);return h.publish(WRAP_OUT,u,content,".retry14-wrap-")

def build_outer(support,materializer,wrapper):
 old=h.verify(BASE,OLD_OUTER_ID,OLD_OUTER_SHA);ss=h.sf(support/"bundle_manifest.json");es=h.sf(support/ENTRY_REL);ms=h.sf(materializer/"bundle_manifest.json");ws=h.sf(wrapper/"bundle_manifest.json");source=(BASE/CONTROLLER).read_text()
 for label,a,b in (("support id",f'SUPPORT_ID = "{OLD_SUPPORT_ID}"',f'SUPPORT_ID = "{support.name}"'),("support sha",f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"',f'SUPPORT_SHA = "{ss}"'),("materializer id",f'MATERIALIZER_ID = "{OLD_MAT_ID}"',f'MATERIALIZER_ID = "{materializer.name}"'),("materializer sha",f'MATERIALIZER_SHA = "{OLD_MAT_SHA}"',f'MATERIALIZER_SHA = "{ms}"'),("wrapper id",f'WRAPPER_ID = "{OLD_WRAP_ID}"',f'WRAPPER_ID = "{wrapper.name}"'),("wrapper sha",f'WRAPPER_SHA = "{OLD_WRAP_SHA}"',f'WRAPPER_SHA = "{ws}"')):source=h.once(source,a,b,"outer "+label)
 source=h.once(source,f'FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT = "tpch-v2-20260803t134500z-b235retry12c10a735e4d91"\nROOT_SYSTEMD_ATTEMPT = "{FAILED}"',f'FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT = "tpch-v2-20260803t134500z-b235retry12c10a735e4d91"\nFAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT = "{FAILED}"\nROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"',"Retry14 attempt");source=h.once(source,f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED}.service"',f'ROOT_SYSTEMD_UNIT = "{UNIT}"',"Retry14 unit");source=h.once(source,f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME/("retry13_root_oneclick_"+FAILED)}")',f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")',"Retry14 output")
 source=h.once(source,'        "failure_retry12_solo_identity": root / "evidence/mysql_tpch_retry12_solo_collector_identity_failure_v1.json",\n','        "failure_retry12_solo_identity": root / "evidence/mysql_tpch_retry12_solo_collector_identity_failure_v1.json",\n        "failure_retry13_python_b_argv": root / "'+FAILURE+'",\n',"failure path")
 source=h.once(source,'        raise OneClickError("Retry12 solo collector identity exact zero-credit evidence differs")\n    return paths\n','''        raise OneClickError("Retry12 solo collector identity exact zero-credit evidence differs")
    retry13 = sealed(paths["failure_retry13_python_b_argv"], "Retry13 python-B argv failure")
    if (retry13.get("schema_version") != "mysql_tpch_retry13_python_b_argv_identity_failure_v1" or retry13.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN" or retry13.get("attempt_token") != FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT or retry13.get("fresh_error") != "retry5 launch failed rc=2 stderr=NOT_READY: solo collector executable identity differs" or retry13.get("current_pids") != {"bootstrap": None, "controller": None, "workload": None} or retry13.get("rerun_prohibited") is not True or retry13.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry13 python-B argv exact zero-credit evidence differs")
    return paths
''',"failure verify")
 source=h.once(source,'    if attempt == FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT:\n        raise OneClickError("failed Retry12 solo-collector identity attempt rerun is prohibited")\n','    if attempt == FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT:\n        raise OneClickError("failed Retry12 solo-collector identity attempt rerun is prohibited")\n    if attempt == FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT:\n        raise OneClickError("failed Retry13 python-B argv attempt rerun is prohibited")\n',"rerun guard")
 source=h.once(source,'        "failed_retry12_solo_identity_evidence": str(paths["failure_retry12_solo_identity"]),\n','        "failed_retry12_solo_identity_evidence": str(paths["failure_retry12_solo_identity"]),\n        "failed_retry13_python_b_argv_attempt": FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT,\n        "failed_retry13_python_b_argv_evidence": str(paths["failure_retry13_python_b_argv"]),\n',"plan")
 source=h.once(source,'            or plan.get("attempt_token") == FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n','            or plan.get("attempt_token") == FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT\n            or plan.get("failed_retry13_python_b_argv_attempt") != FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT\n            or plan.get("attempt_token") == FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',"execute guard")
 skip=("builder/","tests/",f"dependencies/support/{OLD_SUPPORT_ID}/",f"dependencies/materializer/{OLD_MAT_ID}/",f"dependencies/wrapper/{OLD_WRAP_ID}/");content=h.copied(BASE,old,skip);content[CONTROLLER]=source.encode()
 for dep,prefix in ((support,"dependencies/support/"),(materializer,"dependencies/materializer/"),(wrapper,"dependencies/wrapper/")):
  for path in sorted(dep.rglob("*")):
   if path.is_file():content[prefix+dep.name+"/"+path.relative_to(dep).as_posix()]=path.read_bytes()
 failure={"schema_version":"mysql_tpch_retry13_python_b_argv_identity_failure_v1","status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_OUTER_ID,"predecessor_manifest_sha256":OLD_OUTER_SHA,"attempt_token":FAILED,"unit":"ra-mysql-tpch-oneclick-root-"+FAILED+".service","historical_pids":{"root_controller":2367,"controller":14499,"workload":14501},"current_pids":{"bootstrap":None,"controller":None,"workload":None},"debug_samples_observed":[0,1],"fresh_error":"retry5 launch failed rc=2 stderr=NOT_READY: solo collector executable identity differs","root_cause":"sealed interpreter argv includes mandatory -B at index 1 and script at index 2, but four identity checks assumed script at index 1","rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0};raw=h.canonical(failure);content[FAILURE]=raw;content[FAILURE+".sha256"]=(h.sb(raw)+"  "+Path(FAILURE).name+"\n").encode()
 for rel in ("launch/root_start_and_wait_retry9_systemd.sh","launch/run_retry9_root_systemd_principal.sh"):content[rel]=content[rel].decode().replace(FAILED,ATTEMPT).replace("retry13_root_oneclick_"+ATTEMPT,"retry14_root_oneclick_"+ATTEMPT).encode()
 tr="templates/node04_one_click_command.json";t=json.loads(content[tr]);t.update(schema_version="mysql_tpch_retry14_python_b_node04_command_v1",attempt_token=ATTEMPT,systemd_unit=UNIT,support_closure_bundle_id=support.name,failed_retry13_attempt_rerun_prohibited=True);content[tr]=h.canonical(t)
 content["tests/test_mysql_tpch_retry14_python_b_successor_v1.py"]=f'''#!/usr/bin/env python3
import importlib.util,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();S=B/"dependencies/support/{support.name}";M=B/"dependencies/materializer/{materializer.name}";W=B/"dependencies/wrapper/{wrapper.name}";C=B/"{CONTROLLER}"
subprocess.run([sys.executable,"-B",str(S/"tests/test_mysql_tpch_retry14_python_b_argv_identity_v1.py"),str(S)],check=True)
q=importlib.util.spec_from_file_location("o",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package();assert p["support"]==S and p["materializer"]==M and p["wrapper"]==W;assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}"
print("PASS_RETRY14_PYTHON_B_SUCCESSOR")
'''.encode();content["builder/build_mysql_tpch_retry14_python_b_argv_successor_v1.py"]=Path(__file__).read_bytes()
 u=common(old,"mysql_tpch_retry14_python_b_argv_successor_bundle_v1","SEALED_LOCAL_RETRY14_PYTHON_B_ARGV_NOT_EXECUTED",OLD_OUTER_ID,OLD_OUTER_SHA);u.update(support_bundle_id=support.name,support_manifest_sha256=ss,support_entry_sha256=es,support_core_sha256=CORE_SHA,materializer_bundle_id=materializer.name,materializer_manifest_sha256=ms,wrapper_bundle_id=wrapper.name,wrapper_manifest_sha256=ws,failed_retry13_attempt_token=FAILED,failed_retry13_rerun_prohibited=True,new_attempt_token=ATTEMPT,root_systemd_unit=UNIT,python_b_argv_identity_exact=True);return h.publish(OUT,u,content,".retry14-outer-")

def main():
 s=build_support();m=build_materializer(s);w=build_wrapper(s,m);o=build_outer(s,m,w);print(json.dumps({"bundle":str(o),"bundle_id":o.name,"manifest_sha256":h.sf(o/"bundle_manifest.json"),"support_bundle_id":s.name,"support_manifest_sha256":h.sf(s/"bundle_manifest.json"),"support_entry_sha256":h.sf(s/ENTRY_REL),"materializer_bundle_id":m.name,"materializer_manifest_sha256":h.sf(m/"bundle_manifest.json"),"wrapper_bundle_id":w.name,"wrapper_manifest_sha256":h.sf(w/"bundle_manifest.json"),"attempt_token":ATTEMPT},sort_keys=True))
if __name__=="__main__":main()
