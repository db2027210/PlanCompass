#!/usr/bin/env python3
"""Build Retry12 with one exact support-735 closure across all three layers."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTER_OLD_ID = "bee99d5188ac676ecba79094c1b62c41374f38695a38539af7cfd0555587628c"
OUTER_OLD_SHA = "3edffc01c38d9a11937acdfbd5f9f48ab5c51daf9ef34eb3d0a39b79ff927a33"
BASE = ROOT / "artifacts/mysql_tpch_retry11_taskset_systemd219_successor_v1" / OUTER_OLD_ID
MAT_OLD_ID = "d8f8935db191a9c223d28fb3fa29ed0a5ad75afc596ed1908aa9a9275770b360"
MAT_OLD_SHA = "e0408efccad3ac5b0a729ac8f649e800e9f54ad58d1177d174c1e2dfddfad8bd"
WRAP_OLD_ID = "084a7cd468a5c1ddf906f7b0c762874d0afb14521d7faf52feb165ff704845b7"
WRAP_OLD_SHA = "6808bf616f73224f85fd30cc557a63b9efedbbcf5a2c71775a1e2b2f9a02535b"
MAT_OLD = BASE / "dependencies/materializer" / MAT_OLD_ID
WRAP_OLD = BASE / "dependencies/wrapper" / WRAP_OLD_ID
SUPPORT_ID = "735a049e61551796c78dd5fbdd6f4bd19b07d39e176f040d1c59c6577a65bb41"
SUPPORT_SHA = "ddd94bea661081a756db754c3744f9eaa93c670b51cb1090d4b796a6976ba0a3"
ENTRY_SHA = "773b318e0e49c2d266a21ed54da20279b22cc9fecc8d11f150b24034309f87d1"
CORE_SHA = "c3b71ae375822bcb50b5889560dbf57209d7207c37b2506ad3a04c0c7acccece"
SUPPORT = BASE / "dependencies/support" / SUPPORT_ID
FAILED = "tpch-v2-20260803t131500z-b235retry11e07d219a6c4f"
ATTEMPT = "tpch-v2-20260803t134500z-b235retry12c10a735e4d91"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry12_root_oneclick_" + ATTEMPT)
CONTROLLER = "controller/mysql_tpch_retry9_one_click_v1.py"
FAILURE = "evidence/mysql_tpch_retry11_support_closure_mismatch_failure_v1.json"
MAT_OUT = ROOT / "artifacts/mysql_tpch_retry12_support735_materializer_successor_v1"
WRAP_OUT = ROOT / "artifacts/mysql_tpch_retry12_support735_wrapper_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry12_support735_closure_successor_v1"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sb(data):
    return hashlib.sha256(data).hexdigest()


def sf(path):
    return sb(Path(path).read_bytes())


def file_map(root):
    return {p.relative_to(root).as_posix(): sf(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.relative_to(root).as_posix() not in
            ("bundle_manifest.json", "bundle_manifest.json.sha256")}


def verify(root, bundle_id, manifest_sha):
    manifest = json.loads((root / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); unsigned.pop("bundle_id", None)
    if (root.name != bundle_id or manifest.get("bundle_id") != bundle_id
            or sf(root / "bundle_manifest.json") != manifest_sha
            or sb(canonical(unsigned)) != bundle_id
            or file_map(root) != manifest.get("files")):
        raise RuntimeError("bundle closure differs: " + str(root))
    return manifest


def once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(label + " anchor count=" + str(text.count(old)))
    return text.replace(old, new, 1)


def publish(out, unsigned, content, prefix):
    unsigned = dict(unsigned, files={n: sb(v) for n, v in sorted(content.items())})
    bundle_id = sb(canonical(unsigned)); manifest = dict(unsigned, bundle_id=bundle_id)
    raw = canonical(manifest); target = out / bundle_id
    if not target.exists():
        out.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=prefix, dir=out))
        try:
            for name, data in content.items():
                path = stage / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data); path.chmod(0o555 if name.endswith((".py", ".sh")) else 0o444)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json").chmod(0o444)
            (stage / "bundle_manifest.json.sha256").write_text(sb(raw) + "  bundle_manifest.json\n")
            (stage / "bundle_manifest.json.sha256").chmod(0o444)
            for base, dirs, _ in os.walk(stage, topdown=False):
                for directory in dirs: os.chmod(Path(base) / directory, 0o555)
            stage.chmod(0o555); os.replace(stage, target)
        finally:
            if stage.exists(): shutil.rmtree(stage)
    verify(target, bundle_id, sb(raw))
    return target


def copied(root, manifest, skip=()):
    return {name: (root / name).read_bytes() for name in sorted(manifest["files"])
            if not any(name.startswith(prefix) for prefix in skip)}


def build_materializer():
    old = verify(MAT_OLD, MAT_OLD_ID, MAT_OLD_SHA)
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (MAT_OLD / rel).read_text()
    old_constants = ('SUPPORT_ID="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739";'
                     'SUPPORT_SHA="1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7";'
                     'ENTRY_SHA="8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a";'
                     'CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"')
    new_constants = (f'SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";'
                     f'ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"')
    source = once(source, old_constants, new_constants, "materializer support identity")
    content = copied(MAT_OLD, old, ("builder/", "tests/")); content[rel] = source.encode()
    test_rel = "tests/test_mysql_tpch_retry12_support735_materializer_v1.py"
    content[test_rel] = f'''from __future__ import annotations
import hashlib,importlib.util,json,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve(); S=pathlib.Path(sys.argv[2]).resolve(); P=B/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
r=subprocess.run([sys.executable,"-B",str(P),"--help"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
assert r.returncode==0,r.stderr
q=importlib.util.spec_from_file_location("m",P);m=importlib.util.module_from_spec(q);q.loader.exec_module(m)
assert (m.SUPPORT_ID,m.SUPPORT_SHA,m.ENTRY_SHA,m.CORE_SHA)==("{SUPPORT_ID}","{SUPPORT_SHA}","{ENTRY_SHA}","{CORE_SHA}")
root,manifest=m.bundle(S,m.SUPPORT_ID,m.SUPPORT_SHA,"support")
assert m.sha(root/m.ENTRY_REL)==m.ENTRY_SHA and m.sha(root/m.CORE_REL)==m.CORE_SHA
assert manifest["files"][m.ENTRY_REL]==m.ENTRY_SHA and manifest["files"][m.CORE_REL]==m.CORE_SHA
print("PASS_RETRY12_REAL_MATERIALIZER_CLI_SUPPORT735_CLOSURE")
'''.encode()
    content["builder/build_mysql_tpch_retry12_support735_closure_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = {k: v for k, v in old.items() if k not in ("bundle_id", "files", "schema_version", "status", "predecessor_bundle_id", "predecessor_manifest_sha256", "support_bundle_id", "support_manifest_sha256")}
    unsigned.update(schema_version="mysql_tpch_retry12_support735_materializer_successor_bundle_v1",
                    status="SEALED_SUPPORT735_MATERIALIZER_NOT_EXECUTED",
                    predecessor_bundle_id=MAT_OLD_ID, predecessor_manifest_sha256=MAT_OLD_SHA,
                    support_bundle_id=SUPPORT_ID, support_manifest_sha256=SUPPORT_SHA,
                    support_entry_sha256=ENTRY_SHA, support_core_sha256=CORE_SHA,
                    real_cli_closure_test_required=True, formal_launch_authorized=False,
                    accepted_round_authorized=False, accepted_rounds_credit=0, remote_actions=0)
    return publish(MAT_OUT, unsigned, content, ".retry12-mat-")


def build_wrapper(materializer):
    old = verify(WRAP_OLD, WRAP_OLD_ID, WRAP_OLD_SHA); mat_sha = sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"; source = (WRAP_OLD / rel).read_text()
    old_constants = (f'RETRY_ID="{MAT_OLD_ID}";RETRY_SHA="{MAT_OLD_SHA}";FAILED_MEASUREMENT_ATTEMPT=')
    source = once(source, old_constants,
                  f'RETRY_ID="{materializer.name}";RETRY_SHA="{mat_sha}";FAILED_MEASUREMENT_ATTEMPT=',
                  "wrapper materializer identity")
    old_support = ('COLLECTOR_ID="947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739";'
                   'COLLECTOR_SHA="1be67f24372e9893b37be8b717e24ff66d76a33dc40cd73149000abd905e14b7";'
                   'ENTRY_SHA="8287cf1954e634cc298665fa9b71026b9b4ed9612b3d995e868a44a95587035a";'
                   'CORE_SHA="ae6384be782bf4954419dfffbd25a7395719f1cb967bc2477d4414790e2b5317"')
    source = once(source, old_support,
                  f'COLLECTOR_ID="{SUPPORT_ID}";COLLECTOR_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',
                  "wrapper support identity")
    content = copied(WRAP_OLD, old, ("builder/", "tests/")); content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry12_support735_closure_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = {k: v for k, v in old.items() if k not in ("bundle_id", "files", "schema_version", "status", "predecessor_bundle_id", "predecessor_manifest_sha256", "retry5_materializer_bundle_id", "retry5_materializer_manifest_sha256", "collector_bundle_id", "collector_manifest_sha256")}
    unsigned.update(schema_version="mysql_tpch_retry12_support735_wrapper_successor_bundle_v1",
                    status="SEALED_SUPPORT735_WRAPPER_NOT_EXECUTED", predecessor_bundle_id=WRAP_OLD_ID,
                    predecessor_manifest_sha256=WRAP_OLD_SHA, retry5_materializer_bundle_id=materializer.name,
                    retry5_materializer_manifest_sha256=mat_sha, collector_bundle_id=SUPPORT_ID,
                    collector_manifest_sha256=SUPPORT_SHA, collector_entry_sha256=ENTRY_SHA,
                    collector_core_sha256=CORE_SHA, formal_launch_authorized=False,
                    accepted_round_authorized=False, accepted_rounds_credit=0, remote_actions=0)
    return publish(WRAP_OUT, unsigned, content, ".retry12-wrap-")


def build_outer(materializer, wrapper):
    old = verify(BASE, OUTER_OLD_ID, OUTER_OLD_SHA); mat_sha = sf(materializer / "bundle_manifest.json"); wrap_sha = sf(wrapper / "bundle_manifest.json")
    source = (BASE / CONTROLLER).read_text()
    for label, old_value, new_value in (
        ("outer materializer id", f'MATERIALIZER_ID = "{MAT_OLD_ID}"', f'MATERIALIZER_ID = "{materializer.name}"'),
        ("outer materializer sha", f'MATERIALIZER_SHA = "{MAT_OLD_SHA}"', f'MATERIALIZER_SHA = "{mat_sha}"'),
        ("outer wrapper id", f'WRAPPER_ID = "{WRAP_OLD_ID}"', f'WRAPPER_ID = "{wrapper.name}"'),
        ("outer wrapper sha", f'WRAPPER_SHA = "{WRAP_OLD_SHA}"', f'WRAPPER_SHA = "{wrap_sha}"')):
        source = once(source, old_value, new_value, label)
    source = once(source,
        f'FAILED_RETRY10_CPUAFFINITY_ATTEMPT = "tpch-v2-20260803t124500z-b235retry10f6c0a81e4d2b"\nROOT_SYSTEMD_ATTEMPT = "{FAILED}"',
        f'FAILED_RETRY10_CPUAFFINITY_ATTEMPT = "tpch-v2-20260803t124500z-b235retry10f6c0a81e4d2b"\nFAILED_RETRY11_SUPPORT_ATTEMPT = "{FAILED}"\nROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"', "outer Retry12 identity")
    source = once(source, f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED}.service"', f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "unit")
    source = once(source, f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry11_root_oneclick_" + FAILED)}")', f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "output")
    source = once(source, '        "failure_retry10_cpuaffinity": root / "evidence/mysql_tpch_retry10_systemd219_cpuaffinity_failure_v1.json",\n',
                  '        "failure_retry10_cpuaffinity": root / "evidence/mysql_tpch_retry10_systemd219_cpuaffinity_failure_v1.json",\n        "failure_retry11_support": root / "' + FAILURE + '",\n', "failure path")
    source = once(source, '        raise OneClickError("Retry10 CPUAffinity exact zero-credit evidence differs")\n    return paths\n',
'''        raise OneClickError("Retry10 CPUAffinity exact zero-credit evidence differs")
    retry11 = sealed(paths["failure_retry11_support"], "Retry11 support closure failure")
    if (retry11.get("schema_version") != "mysql_tpch_retry11_support_closure_mismatch_failure_v1"
            or retry11.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN"
            or retry11.get("attempt_token") != FAILED_RETRY11_SUPPORT_ATTEMPT
            or retry11.get("error") != "Retry9 workload materializer failed rc=2 stderr=NOT_READY: support differs"
            or retry11.get("controller_pid") is not None or retry11.get("workload_pid") is not None
            or retry11.get("rerun_prohibited") is not True or retry11.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry11 support closure exact zero-credit evidence differs")
    return paths
''', "failure verify")
    source = once(source, '    if attempt == FAILED_RETRY10_CPUAFFINITY_ATTEMPT:\n        raise OneClickError("failed Retry10 CPUAffinity attempt rerun is prohibited")\n',
                  '    if attempt == FAILED_RETRY10_CPUAFFINITY_ATTEMPT:\n        raise OneClickError("failed Retry10 CPUAffinity attempt rerun is prohibited")\n    if attempt == FAILED_RETRY11_SUPPORT_ATTEMPT:\n        raise OneClickError("failed Retry11 support-closure attempt rerun is prohibited")\n', "materialize guard")
    source = once(source, '        "failed_retry10_cpuaffinity_evidence": str(paths["failure_retry10_cpuaffinity"]),\n',
                  '        "failed_retry10_cpuaffinity_evidence": str(paths["failure_retry10_cpuaffinity"]),\n        "failed_retry11_support_attempt": FAILED_RETRY11_SUPPORT_ATTEMPT,\n        "failed_retry11_support_evidence": str(paths["failure_retry11_support"]),\n', "plan binding")
    source = once(source, '            or plan.get("attempt_token") == FAILED_RETRY10_CPUAFFINITY_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',
                  '            or plan.get("attempt_token") == FAILED_RETRY10_CPUAFFINITY_ATTEMPT\n            or plan.get("failed_retry11_support_attempt") != FAILED_RETRY11_SUPPORT_ATTEMPT\n            or plan.get("attempt_token") == FAILED_RETRY11_SUPPORT_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n', "execute guard")
    skip = ("builder/", "tests/", f"dependencies/materializer/{MAT_OLD_ID}/", f"dependencies/wrapper/{WRAP_OLD_ID}/")
    content = copied(BASE, old, skip); content[CONTROLLER] = source.encode()
    for dep, prefix in ((materializer, "dependencies/materializer/"), (wrapper, "dependencies/wrapper/")):
        for path in sorted(dep.rglob("*")):
            if path.is_file(): content[prefix + dep.name + "/" + path.relative_to(dep).as_posix()] = path.read_bytes()
    failure = {"schema_version":"mysql_tpch_retry11_support_closure_mismatch_failure_v1","status":"FAILED_ZERO_CREDIT_DO_NOT_RERUN","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"predecessor_bundle_id":OUTER_OLD_ID,"predecessor_manifest_sha256":OUTER_OLD_SHA,"attempt_token":FAILED,"bootstrap_passed":True,"controller_pid":None,"workload_pid":None,"error":"Retry9 workload materializer failed rc=2 stderr=NOT_READY: support differs","embedded_support_bundle_id":SUPPORT_ID,"stale_materializer_support_bundle_id":"947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739","root_cause":"nested materializer and wrapper retained stale support identity while outer embedded support735","rerun_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
    raw = canonical(failure); content[FAILURE] = raw; content[FAILURE + ".sha256"] = (sb(raw) + "  " + Path(FAILURE).name + "\n").encode()
    for rel in ("launch/root_start_and_wait_retry9_systemd.sh", "launch/run_retry9_root_systemd_principal.sh"):
        text = content[rel].decode().replace(FAILED, ATTEMPT).replace("retry11_root_oneclick_" + ATTEMPT, "retry12_root_oneclick_" + ATTEMPT)
        content[rel] = text.encode()
    template_rel = "templates/node04_one_click_command.json"; template = json.loads(content[template_rel]); template.update(schema_version="mysql_tpch_retry12_support735_node04_command_v1", attempt_token=ATTEMPT, systemd_unit=UNIT, support_closure_bundle_id=SUPPORT_ID, stale_retry11_attempt_rerun_prohibited=True); content[template_rel] = canonical(template)
    test_rel = "tests/test_mysql_tpch_retry12_support735_closure_successor_v1.py"
    content[test_rel] = f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve(); S=B/"dependencies/support/{SUPPORT_ID}"; M=next((B/"dependencies/materializer").iterdir()); W=next((B/"dependencies/wrapper").iterdir()); C=B/"controller/mysql_tpch_retry9_one_click_v1.py"
r=subprocess.run([sys.executable,"-B",str(M/"materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"),"--help"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);assert r.returncode==0,r.stderr
subprocess.run([sys.executable,"-B",str(M/"tests/test_mysql_tpch_retry12_support735_materializer_v1.py"),str(M),str(S)],check=True)
q=importlib.util.spec_from_file_location("o",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package()
assert p["support"]==S and p["materializer"]==M and p["wrapper"]==W
assert o.MATERIALIZER_ID==M.name and o.WRAPPER_ID==W.name and o.SUPPORT_ID==S.name
WP=W/"controller/mysql_tpch_retry5_systemd219_completion_v1.py";r=subprocess.run([sys.executable,"-B",str(WP),"--help"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);assert r.returncode==0,r.stderr
q=importlib.util.spec_from_file_location("w",WP);w=importlib.util.module_from_spec(q);q.loader.exec_module(w)
assert (w.RETRY_ID,w.RETRY_SHA,w.COLLECTOR_ID,w.COLLECTOR_SHA,w.ENTRY_SHA,w.CORE_SHA)==(M.name,"{mat_sha}",S.name,"{SUPPORT_SHA}","{ENTRY_SHA}","{CORE_SHA}")
assert "failed Retry11 support-closure attempt rerun is prohibited" in C.read_text()
print("PASS_RETRY12_THREE_LAYER_SUPPORT735_CLOSURE")
'''.encode()
    content["builder/build_mysql_tpch_retry12_support735_closure_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = {k:v for k,v in old.items() if k not in ("bundle_id","files","schema_version","status","predecessor_bundle_id","predecessor_manifest_sha256","new_attempt_token","root_systemd_unit")}
    unsigned.update(schema_version="mysql_tpch_retry12_support735_closure_successor_bundle_v1", status="SEALED_LOCAL_RETRY12_SUPPORT735_CLOSURE_NOT_EXECUTED", predecessor_bundle_id=OUTER_OLD_ID, predecessor_manifest_sha256=OUTER_OLD_SHA, failed_retry11_attempt_token=FAILED, failed_retry11_rerun_prohibited=True, new_attempt_token=ATTEMPT, root_systemd_unit=UNIT, support_bundle_id=SUPPORT_ID, support_manifest_sha256=SUPPORT_SHA, support_entry_sha256=ENTRY_SHA, support_core_sha256=CORE_SHA, materializer_bundle_id=materializer.name, materializer_manifest_sha256=mat_sha, wrapper_bundle_id=wrapper.name, wrapper_manifest_sha256=wrap_sha, three_layer_support_identity_exact=True, real_materializer_cli_closure_test_required=True, formal_launch_authorized=False, accepted_round_authorized=False, accepted_rounds_credit=0, remote_actions=0)
    return publish(OUT, unsigned, content, ".retry12-outer-")


def main():
    verify(SUPPORT, SUPPORT_ID, SUPPORT_SHA)
    materializer = build_materializer(); wrapper = build_wrapper(materializer); outer = build_outer(materializer, wrapper)
    print(json.dumps({"bundle":str(outer),"bundle_id":outer.name,"manifest_sha256":sf(outer/"bundle_manifest.json"),"materializer_bundle_id":materializer.name,"materializer_manifest_sha256":sf(materializer/"bundle_manifest.json"),"wrapper_bundle_id":wrapper.name,"wrapper_manifest_sha256":sf(wrapper/"bundle_manifest.json"),"support_bundle_id":SUPPORT_ID,"support_manifest_sha256":SUPPORT_SHA,"attempt_token":ATTEMPT},sort_keys=True))


if __name__ == "__main__": main()
