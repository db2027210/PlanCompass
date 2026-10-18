#!/usr/bin/env python3
"""Build Retry13: validate both solo and paired collector control identities."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "shell/build_mysql_tpch_retry12_support735_closure_successor_v1.py"
spec = importlib.util.spec_from_file_location("retry12_builder", HELPER_PATH)
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)

OLD_OUTER_ID = "ffabec6184e675d7e1e82356ec5bf0f99a3afa52ccf9bd2304994312c1860293"
OLD_OUTER_SHA = "83df958670287779b2a0b15d829afe5805eff2b08d0e688b66cc3056346ea13f"
BASE = ROOT / "artifacts/mysql_tpch_retry12_support735_closure_successor_v1" / OLD_OUTER_ID
OLD_SUPPORT_ID = "735a049e61551796c78dd5fbdd6f4bd19b07d39e176f040d1c59c6577a65bb41"
OLD_SUPPORT_SHA = "ddd94bea661081a756db754c3744f9eaa93c670b51cb1090d4b796a6976ba0a3"
OLD_ENTRY_SHA = "773b318e0e49c2d266a21ed54da20279b22cc9fecc8d11f150b24034309f87d1"
CORE_SHA = "c3b71ae375822bcb50b5889560dbf57209d7207c37b2506ad3a04c0c7acccece"
OLD_MAT_ID = "1ca5485d3a95b7f08d02e3ccbce5a6179a7cc7e6df51d4d70da9c5e6e5a536ec"
OLD_MAT_SHA = "96c3b2a2c66eb4c70ebaf54a6ec1d787433e3711192cccb5c6f28969e16743d4"
OLD_WRAP_ID = "3ab237a2f5bda3a4a8c8a99dab9e51745f60aaaf885ed05daef1fd31b674ae8c"
OLD_WRAP_SHA = "de39bde6d458458d7c5287a7c841f803f39afaf4c4945b5349e8219a11b15348"
SUPPORT_OLD = BASE / "dependencies/support" / OLD_SUPPORT_ID
MAT_OLD = BASE / "dependencies/materializer" / OLD_MAT_ID
WRAP_OLD = BASE / "dependencies/wrapper" / OLD_WRAP_ID
FAILED = "tpch-v2-20260803t134500z-b235retry12c10a735e4d91"
ATTEMPT = "tpch-v2-20260803t214500p08-b235retry13solo7c8f1"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry13_root_oneclick_" + ATTEMPT)
CONTROLLER = "controller/mysql_tpch_retry9_one_click_v1.py"
FAILURE = "evidence/mysql_tpch_retry12_solo_collector_identity_failure_v1.json"
ENTRY_REL = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
CORE_REL = "qualification/mysql_distributed_live_readback_qualification_v1.py"
SUPPORT_OUT = ROOT / "artifacts/mysql_tpch_retry13_solo_collector_support_successor_v1"
MAT_OUT = ROOT / "artifacts/mysql_tpch_retry13_solo_collector_materializer_successor_v1"
WRAP_OUT = ROOT / "artifacts/mysql_tpch_retry13_solo_collector_wrapper_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry13_solo_collector_identity_successor_v1"


def common(old, schema, status, predecessor_id, predecessor_sha):
    value = {k:v for k,v in old.items() if k not in
             ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256")}
    value.update(schema_version=schema, status=status, predecessor_bundle_id=predecessor_id,
                 predecessor_manifest_sha256=predecessor_sha, formal_scope="mysql_only",
                 cross_engine_comparison_prohibited=True, postgresql_mutation_prohibited=True,
                 formal_launch_authorized=False, accepted_round_authorized=False,
                 accepted_rounds_credit=0, remote_actions=0)
    return value


def build_support():
    old = h.verify(SUPPORT_OLD, OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    source = (SUPPORT_OLD / ENTRY_REL).read_text()
    source = h.once(source, "def _sealed_collector_command(argv):\n", "def _sealed_paired_collector_command(argv):\n", "rename paired validator")
    anchor = '    return command\n\n\ndef _classify_verified_collector_trees(result, mysql_cpuset, workload_cpuset):\n'
    replacement = '''    return command


def _sealed_solo_collector_command(argv):
    entry = str(Path(__file__).resolve())
    if (len(argv) < 3 or Path(argv[0]).resolve() != Path(sys.executable).resolve()
            or Path(argv[1]).resolve() != Path(entry)
            or argv.count("--qualification-solo") != 1
            or "--qualification-paired" in argv):
        raise core.QualificationError("solo collector executable identity differs")
    command_path = Path(_exact_option(argv, "--command-spec"))
    authority_path = Path(_exact_option(argv, "--main-agent-authority"))
    token_path = Path(_exact_option(argv, "--main-agent-token-file"))
    command = core.load_object(command_path, "solo collector command")
    authority = core.load_object(authority_path, "solo collector authority")
    reject_static_pid_authority(command, "solo command")
    reject_static_pid_authority(authority, "solo authority")
    token_state = token_path.stat(follow_symlinks=False)
    token = token_path.read_text(encoding="ascii").strip()
    manifest_path = HERE.parent / "bundle_manifest.json"
    manifest = core.load_object(manifest_path, "collector manifest")
    binding = command.get("retry5_completion_binding") or {}
    flags = ("capture_authorized", "formal_r0_authorized", "formal_authorized", "accepted_round_authorized")
    now = time.time()
    if (not core.valid_sidecar(command_path) or not core.valid_sidecar(authority_path)
            or token_path.is_symlink() or not token_path.is_file()
            or stat.S_IMODE(token_state.st_mode) != 0o600 or token_state.st_uid != 0
            or len(token) < 48 or any(character.isspace() for character in token)
            or command.get("schema_version") != "mysql_algorithm_c_debug_qualification_command_v1"
            or command.get("mode") != "qualification_solo" or command.get("debug_only") is not True
            or any(command.get(key) is not False for key in flags)
            or authority.get("schema_version") != "mysql_algorithm_c_main_agent_qualification_authority_v1"
            or authority.get("authorized_by") != "main_agent" or authority.get("mode") != "qualification_solo"
            or authority.get("debug_only") is not True or any(authority.get(key) is not False for key in flags)
            or authority.get("qualification_attempt_id") != command.get("qualification_attempt_id")
            or authority.get("bundle_id") != command.get("bundle_id")
            or authority.get("command_spec_sha256") != core.sha256(command_path)
            or authority.get("main_agent_token_sha256") != core.sha_bytes(token.encode("ascii"))
            or authority.get("support_collector_bundle_id") != HERE.parent.name
            or authority.get("support_collector_manifest_sha256") != core.sha256(manifest_path)
            or authority.get("support_collector_entry_sha256") != core.sha256(Path(__file__))
            or authority.get("support_collector_core_sha256") != core.sha256(CORE_PATH)
            or binding.get("support_collector_bundle_id") != HERE.parent.name
            or binding.get("support_collector_manifest_sha256") != core.sha256(manifest_path)
            or binding.get("support_collector_entry_sha256") != core.sha256(Path(__file__))
            or binding.get("support_collector_core_sha256") != core.sha256(CORE_PATH)
            or manifest.get("bundle_id") != HERE.parent.name
            or not float(authority.get("not_before_epoch") or 0) <= now <= float(authority.get("expires_epoch") or 0)):
        raise core.QualificationError("solo collector signed authority differs")
    return command


def _sealed_collector_command(argv):
    if argv.count("--qualification-solo") == 1 and "--qualification-paired" not in argv:
        return _sealed_solo_collector_command(argv)
    return _sealed_paired_collector_command(argv)


def _classify_verified_collector_trees(result, mysql_cpuset, workload_cpuset):
'''
    source = h.once(source, anchor, replacement, "solo/paired dispatcher")
    content = h.copied(SUPPORT_OLD, old, ("builder/","tests/")); content[ENTRY_REL] = source.encode()
    test_rel = "tests/test_mysql_tpch_retry13_solo_collector_identity_v1.py"
    content[test_rel] = '''#!/usr/bin/env python3
import importlib.util,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
q=importlib.util.spec_from_file_location("c",P);c=importlib.util.module_from_spec(q);q.loader.exec_module(c)
argv=[sys.executable,str(P),"--qualification-solo","--command-spec","/absent-command","--main-agent-authority","/absent-authority","--main-agent-token-file","/absent-token"]
try:c._sealed_collector_command(argv)
except Exception as e:
 assert "paired collector executable identity differs" not in str(e);assert "solo collector executable identity differs" not in str(e)
else:raise AssertionError("absent sealed inputs unexpectedly passed")
assert c._sealed_collector_command.__name__=="_sealed_collector_command"
print("PASS_RETRY13_SOLO_COLLECTOR_DISPATCH")
'''.encode()
    content["builder/build_mysql_tpch_retry13_solo_collector_identity_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry13_solo_collector_support_successor_bundle_v1", "SEALED_SOLO_AND_PAIRED_COLLECTOR_IDENTITY_NOT_EXECUTED", OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    unsigned.update(solo_collector_identity_supported=True, paired_collector_identity_preserved=True,
                    qualification_mode_dispatch_fail_closed=True, failed_retry12_attempt_token=FAILED,
                    failed_retry12_rerun_prohibited=True)
    return h.publish(SUPPORT_OUT, unsigned, content, ".retry13-support-")


def build_materializer(support):
    old = h.verify(MAT_OLD, OLD_MAT_ID, OLD_MAT_SHA); support_sha=h.sf(support/"bundle_manifest.json"); entry_sha=h.sf(support/ENTRY_REL)
    rel="materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py";source=(MAT_OLD/rel).read_text()
    oldc=f'SUPPORT_ID="{OLD_SUPPORT_ID}";SUPPORT_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{CORE_SHA}"'
    newc=f'SUPPORT_ID="{support.name}";SUPPORT_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{CORE_SHA}"'
    source=h.once(source,oldc,newc,"materializer Retry13 support")
    content=h.copied(MAT_OLD,old,("builder/","tests/"));content[rel]=source.encode();content["builder/build_mysql_tpch_retry13_solo_collector_identity_successor_v1.py"]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry13_solo_collector_materializer_successor_bundle_v1","SEALED_RETRY13_MATERIALIZER_NOT_EXECUTED",OLD_MAT_ID,OLD_MAT_SHA)
    unsigned.update(support_bundle_id=support.name,support_manifest_sha256=support_sha,support_entry_sha256=entry_sha,support_core_sha256=CORE_SHA)
    return h.publish(MAT_OUT,unsigned,content,".retry13-mat-")


def build_wrapper(support,materializer):
    old=h.verify(WRAP_OLD,OLD_WRAP_ID,OLD_WRAP_SHA);support_sha=h.sf(support/"bundle_manifest.json");entry_sha=h.sf(support/ENTRY_REL);mat_sha=h.sf(materializer/"bundle_manifest.json")
    rel="controller/mysql_tpch_retry5_systemd219_completion_v1.py";source=(WRAP_OLD/rel).read_text()
    source=h.once(source,f'RETRY_ID="{OLD_MAT_ID}";RETRY_SHA="{OLD_MAT_SHA}"',f'RETRY_ID="{materializer.name}";RETRY_SHA="{mat_sha}"',"wrapper Retry13 materializer")
    oldc=f'COLLECTOR_ID="{OLD_SUPPORT_ID}";COLLECTOR_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{CORE_SHA}"'
    newc=f'COLLECTOR_ID="{support.name}";COLLECTOR_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{CORE_SHA}"'
    source=h.once(source,oldc,newc,"wrapper Retry13 support")
    content=h.copied(WRAP_OLD,old,("builder/","tests/"));content[rel]=source.encode();content["builder/build_mysql_tpch_retry13_solo_collector_identity_successor_v1.py"]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry13_solo_collector_wrapper_successor_bundle_v1","SEALED_RETRY13_WRAPPER_NOT_EXECUTED",OLD_WRAP_ID,OLD_WRAP_SHA)
    unsigned.update(retry5_materializer_bundle_id=materializer.name,retry5_materializer_manifest_sha256=mat_sha,collector_bundle_id=support.name,collector_manifest_sha256=support_sha,collector_entry_sha256=entry_sha,collector_core_sha256=CORE_SHA)
    return h.publish(WRAP_OUT,unsigned,content,".retry13-wrap-")


def build_outer(support,materializer,wrapper):
    old=h.verify(BASE,OLD_OUTER_ID,OLD_OUTER_SHA);support_sha=h.sf(support/"bundle_manifest.json");entry_sha=h.sf(support/ENTRY_REL);mat_sha=h.sf(materializer/"bundle_manifest.json");wrap_sha=h.sf(wrapper/"bundle_manifest.json")
    source=(BASE/CONTROLLER).read_text()
    for label,a,b in (("support id",f'SUPPORT_ID = "{OLD_SUPPORT_ID}"',f'SUPPORT_ID = "{support.name}"'),("support sha",f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"',f'SUPPORT_SHA = "{support_sha}"'),("materializer id",f'MATERIALIZER_ID = "{OLD_MAT_ID}"',f'MATERIALIZER_ID = "{materializer.name}"'),("materializer sha",f'MATERIALIZER_SHA = "{OLD_MAT_SHA}"',f'MATERIALIZER_SHA = "{mat_sha}"'),("wrapper id",f'WRAPPER_ID = "{OLD_WRAP_ID}"',f'WRAPPER_ID = "{wrapper.name}"'),("wrapper sha",f'WRAPPER_SHA = "{OLD_WRAP_SHA}"',f'WRAPPER_SHA = "{wrap_sha}"')):source=h.once(source,a,b,"outer "+label)
    source=h.once(source,f'FAILED_RETRY11_SUPPORT_ATTEMPT = "tpch-v2-20260803t131500z-b235retry11e07d219a6c4f"\nROOT_SYSTEMD_ATTEMPT = "{FAILED}"',f'FAILED_RETRY11_SUPPORT_ATTEMPT = "tpch-v2-20260803t131500z-b235retry11e07d219a6c4f"\nFAILED_RETRY12_SOLO_IDENTITY_ATTEMPT = "{FAILED}"\nROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"',"Retry13 attempt")
    source=h.once(source,f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED}.service"',f'ROOT_SYSTEMD_UNIT = "{UNIT}"',"Retry13 unit")
    source=h.once(source,f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME/("retry12_root_oneclick_"+FAILED)}")',f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")',"Retry13 output")
    source=h.once(source,'        "failure_retry11_support": root / "evidence/mysql_tpch_retry11_support_closure_mismatch_failure_v1.json",\n','        "failure_retry11_support": root / "evidence/mysql_tpch_retry11_support_closure_mismatch_failure_v1.json",\n        "failure_retry12_solo_identity": root / "'+FAILURE+'",\n',"Retry12 failure path")
    source=h.once(source,'        raise OneClickError("Retry11 support closure exact zero-credit evidence differs")\n    return paths\n','''        raise OneClickError("Retry11 support closure exact zero-credit evidence differs")
    retry12 = sealed(paths["failure_retry12_solo_identity"], "Retry12 solo collector identity failure")
    if (retry12.get("schema_version") != "mysql_tpch_retry12_solo_collector_identity_failure_v1"
            or retry12.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN"
            or retry12.get("attempt_token") != FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT
            or retry12.get("fresh_error") != "retry5 launch failed rc=2 stderr=NOT_READY: paired collector executable identity differs"
            or retry12.get("current_pids") != {"bootstrap": None, "controller": None, "workload": None}
            or retry12.get("post_failure_reconcile_passed") is not True
            or retry12.get("post_failure_closure_passed") is not True
            or retry12.get("rerun_prohibited") is not True or retry12.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry12 solo collector identity exact zero-credit evidence differs")
    return paths
''',"Retry12 failure verify")
    source=h.once(source,'    if attempt == FAILED_RETRY11_SUPPORT_ATTEMPT:\n        raise OneClickError("failed Retry11 support-closure attempt rerun is prohibited")\n','    if attempt == FAILED_RETRY11_SUPPORT_ATTEMPT:\n        raise OneClickError("failed Retry11 support-closure attempt rerun is prohibited")\n    if attempt == FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT:\n        raise OneClickError("failed Retry12 solo-collector identity attempt rerun is prohibited")\n',"Retry12 rerun guard")
    source=h.once(source,'        "failed_retry11_support_evidence": str(paths["failure_retry11_support"]),\n','        "failed_retry11_support_evidence": str(paths["failure_retry11_support"]),\n        "failed_retry12_solo_identity_attempt": FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT,\n        "failed_retry12_solo_identity_evidence": str(paths["failure_retry12_solo_identity"]),\n',"Retry12 plan")
    source=h.once(source,'            or plan.get("attempt_token") == FAILED_RETRY11_SUPPORT_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n','            or plan.get("attempt_token") == FAILED_RETRY11_SUPPORT_ATTEMPT\n            or plan.get("failed_retry12_solo_identity_attempt") != FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT\n            or plan.get("attempt_token") == FAILED_RETRY12_SOLO_IDENTITY_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',"Retry12 execute guard")
    skip=("builder/","tests/",f"dependencies/support/{OLD_SUPPORT_ID}/",f"dependencies/materializer/{OLD_MAT_ID}/",f"dependencies/wrapper/{OLD_WRAP_ID}/")
    content=h.copied(BASE,old,skip);content[CONTROLLER]=source.encode()
    for dep,prefix in ((support,"dependencies/support/"),(materializer,"dependencies/materializer/"),(wrapper,"dependencies/wrapper/")):
        for path in sorted(dep.rglob("*")):
            if path.is_file():content[prefix+dep.name+"/"+path.relative_to(dep).as_posix()]=path.read_bytes()
    failure={"schema_version":"mysql_tpch_retry12_solo_collector_identity_failure_v1","status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OLD_OUTER_ID,"predecessor_manifest_sha256":OLD_OUTER_SHA,"attempt_token":FAILED,"unit":"ra-mysql-tpch-oneclick-root-"+FAILED+".service","started_at":"2026-08-03T20:59:16+08:00","failed_at":"2026-08-03T21:13:26+08:00","historical_pids":{"bootstrap":81291,"controller":92785,"workload":92788},"current_pids":{"bootstrap":None,"controller":None,"workload":None},"fresh_error":"retry5 launch failed rc=2 stderr=NOT_READY: paired collector executable identity differs","root_cause":"qualification_solo ancestry validation unconditionally called paired-only executable validator","post_failure_reconcile_passed":True,"post_failure_closure_passed":True,"containers_reconciled_absent":True,"rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
    raw=h.canonical(failure);content[FAILURE]=raw;content[FAILURE+".sha256"]=(h.sb(raw)+"  "+Path(FAILURE).name+"\n").encode()
    for rel in ("launch/root_start_and_wait_retry9_systemd.sh","launch/run_retry9_root_systemd_principal.sh"):
        content[rel]=content[rel].decode().replace(FAILED,ATTEMPT).replace("retry12_root_oneclick_"+ATTEMPT,"retry13_root_oneclick_"+ATTEMPT).encode()
    template_rel="templates/node04_one_click_command.json";template=json.loads(content[template_rel]);template.update(schema_version="mysql_tpch_retry13_solo_collector_node04_command_v1",attempt_token=ATTEMPT,systemd_unit=UNIT,support_closure_bundle_id=support.name,failed_retry12_attempt_rerun_prohibited=True);content[template_rel]=h.canonical(template)
    test_rel="tests/test_mysql_tpch_retry13_solo_collector_successor_v1.py";content[test_rel]=f'''#!/usr/bin/env python3
import importlib.util,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();S=B/"dependencies/support/{support.name}";M=B/"dependencies/materializer/{materializer.name}";W=B/"dependencies/wrapper/{wrapper.name}";C=B/"controller/mysql_tpch_retry9_one_click_v1.py"
subprocess.run([sys.executable,"-B",str(S/"tests/test_mysql_tpch_retry13_solo_collector_identity_v1.py"),str(S)],check=True)
q=importlib.util.spec_from_file_location("o",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package();assert p["support"]==S and p["materializer"]==M and p["wrapper"]==W
assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and "failed Retry12 solo-collector identity attempt rerun is prohibited" in C.read_text()
print("PASS_RETRY13_SOLO_COLLECTOR_SUCCESSOR")
'''.encode();content["builder/build_mysql_tpch_retry13_solo_collector_identity_successor_v1.py"]=Path(__file__).read_bytes()
    unsigned=common(old,"mysql_tpch_retry13_solo_collector_identity_successor_bundle_v1","SEALED_LOCAL_RETRY13_SOLO_COLLECTOR_NOT_EXECUTED",OLD_OUTER_ID,OLD_OUTER_SHA)
    unsigned.update(support_bundle_id=support.name,support_manifest_sha256=support_sha,support_entry_sha256=entry_sha,support_core_sha256=CORE_SHA,materializer_bundle_id=materializer.name,materializer_manifest_sha256=mat_sha,wrapper_bundle_id=wrapper.name,wrapper_manifest_sha256=wrap_sha,failed_retry12_attempt_token=FAILED,failed_retry12_rerun_prohibited=True,new_attempt_token=ATTEMPT,root_systemd_unit=UNIT,solo_and_paired_collector_identity_supported=True)
    return h.publish(OUT,unsigned,content,".retry13-outer-")


def main():
    support=build_support();materializer=build_materializer(support);wrapper=build_wrapper(support,materializer);outer=build_outer(support,materializer,wrapper)
    print(json.dumps({"bundle":str(outer),"bundle_id":outer.name,"manifest_sha256":h.sf(outer/"bundle_manifest.json"),"support_bundle_id":support.name,"support_manifest_sha256":h.sf(support/"bundle_manifest.json"),"support_entry_sha256":h.sf(support/ENTRY_REL),"materializer_bundle_id":materializer.name,"materializer_manifest_sha256":h.sf(materializer/"bundle_manifest.json"),"wrapper_bundle_id":wrapper.name,"wrapper_manifest_sha256":h.sf(wrapper/"bundle_manifest.json"),"attempt_token":ATTEMPT},sort_keys=True))


if __name__=="__main__":main()
