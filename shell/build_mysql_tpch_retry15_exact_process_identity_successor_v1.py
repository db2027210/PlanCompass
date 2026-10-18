#!/usr/bin/env python3
"""Build Retry15 with exact process identity for co-resident inventory."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry14_builder", ROOT / "shell/build_mysql_tpch_retry14_python_b_argv_successor_v1.py"
)
retry14 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retry14)
h = retry14.h

OLD_OUTER_ID = "380c2fcad531559804501c1ac3e7f10989f84697633687949c51e62dbbd4e888"
OLD_OUTER_SHA = "9994888b716b8bd58979b9a4c90854f00810edbc61e808dbb64e30f20f63fad6"
BASE = ROOT / "artifacts/mysql_tpch_retry14_python_b_argv_successor_v1" / OLD_OUTER_ID
OLD_SUPPORT_ID = "7ca87bb8217b9879b7af950a66eb094accef4f4cbc65495f2f99041ca77a36eb"
OLD_SUPPORT_SHA = "f68351190ff395861bf02038dec74bb944e20500edcc190ff7fb033b0a145fa6"
OLD_ENTRY_SHA = "5f1e27816dd725ec40c1c4711c5a67423db64362389a4f67e2d9a29fdc16be5a"
OLD_CORE_SHA = "c3b71ae375822bcb50b5889560dbf57209d7207c37b2506ad3a04c0c7acccece"
OLD_MAT_ID = "94cbc050d8fa23a681b4edacd15164f0b4a4f9e859f1c0ab7eacda38c48aa5f2"
OLD_MAT_SHA = "5f37582c116cac88525db9b5cff04a757bbdc8d8bd1f920e3bf686cd7ff78e47"
OLD_WRAP_ID = "d2593a7760efbae79cb24acbe9e5df295ddf144f87bee408cd7cdd10b0e18532"
OLD_WRAP_SHA = "fc2437c0cdca148ac7b3381a28a786a549f66b8a2b19947a5bab651915fcc20f"
SUPPORT_OLD = BASE / "dependencies/support" / OLD_SUPPORT_ID
MAT_OLD = BASE / "dependencies/materializer" / OLD_MAT_ID
WRAP_OLD = BASE / "dependencies/wrapper" / OLD_WRAP_ID
ENTRY_REL = retry14.ENTRY_REL
CORE_REL = retry14.CORE_REL
CONTROLLER = retry14.CONTROLLER
RUNTIME = retry14.RUNTIME
FAILED = "tpch-v2-20260803t220000p08-b235retry14argv2d6e9"
ATTEMPT = "tpch-v2-20260803t221500p08-b235retry15procid4e2a"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
OUTPUT = RUNTIME / ("retry15_root_oneclick_" + ATTEMPT)
FAILURE = "evidence/mysql_tpch_retry14_process_identity_false_positive_failure_v1.json"
SUPPORT_OUT = ROOT / "artifacts/mysql_tpch_retry15_exact_process_identity_support_successor_v1"
MAT_OUT = ROOT / "artifacts/mysql_tpch_retry15_exact_process_identity_materializer_successor_v1"
WRAP_OUT = ROOT / "artifacts/mysql_tpch_retry15_exact_process_identity_wrapper_successor_v1"
OUT = ROOT / "artifacts/mysql_tpch_retry15_exact_process_identity_successor_v1"


def common(old, schema, status, predecessor_id, predecessor_sha):
    value = {
        key: item for key, item in old.items()
        if key not in ("bundle_id", "files", "schema_version", "status",
                       "predecessor_bundle_id", "predecessor_manifest_sha256")
    }
    value.update(
        schema_version=schema,
        status=status,
        predecessor_bundle_id=predecessor_id,
        predecessor_manifest_sha256=predecessor_sha,
        formal_scope="mysql_only",
        cross_engine_comparison_prohibited=True,
        postgresql_mutation_prohibited=True,
        formal_launch_authorized=False,
        accepted_round_authorized=False,
        accepted_rounds_credit=0,
        remote_actions=0,
    )
    return value


def build_support():
    old = h.verify(SUPPORT_OLD, OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    core = (SUPPORT_OLD / CORE_REL).read_text()
    marker = "\n\nclass QualificationError(RuntimeError):\n"
    helper = r'''

def database_or_benchmark_process_identity(comm, argv_tokens):
    """Match executable identity, never arbitrary text embedded in ``sh -c``."""
    if not isinstance(comm, str) or not isinstance(argv_tokens, list):
        return False
    if CORESIDENT_PROCESS_PATTERN.search(comm):
        return True
    for token in argv_tokens:
        if not isinstance(token, str) or not token or any(ch.isspace() for ch in token):
            continue
        if CORESIDENT_PROCESS_PATTERN.search(os.path.basename(token)):
            return True
    return False
'''
    core = h.once(core, marker, helper + marker, "exact process identity helper")
    old_cmd = '''        command = raw_cmdline.replace(b"\\0", b" ").decode(
            "utf-8", errors="replace"
        ).strip()
'''
    new_cmd = '''        argv_tokens = [
            token.decode("utf-8", errors="replace")
            for token in raw_cmdline.split(b"\\0") if token
        ]
        command = " ".join(argv_tokens)
'''
    core = h.once(core, old_cmd, new_cmd, "argv token capture")
    core = h.once(core, '            "_command": command,\n',
                  '            "_command": command,\n            "_argv_tokens": argv_tokens,\n',
                  "argv token inventory")
    old_relevance = '''            or CORESIDENT_PROCESS_PATTERN.search(
                row["comm"] + " " + row["_command"]
            )
'''
    new_relevance = '''            or database_or_benchmark_process_identity(
                row["comm"], row["_argv_tokens"]
            )
'''
    core = h.once(core, old_relevance, new_relevance, "identity relevance")
    content = h.copied(SUPPORT_OLD, old, ("builder/", "tests/"))
    content[CORE_REL] = core.encode()
    content["builder/build_mysql_tpch_retry15_exact_process_identity_successor_v1.py"] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry15_exact_process_identity_v1.py"] = f'''#!/usr/bin/env python3
import importlib.util,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();P=B/"{CORE_REL}"
q=importlib.util.spec_from_file_location("core",P);c=importlib.util.module_from_spec(q);q.loader.exec_module(c)
yes=[("postgres",[]),("mysqld",[]),("python3",["python3","/opt/mysql_tpch_runner.py"]),("java",["java","/opt/benchbase.jar"]),("sh",["sh","/opt/run_tpcc.sh"])]
no=[("bash",["bash","-c","cd /mnt/pg/tpch_node04/tmp && python3 -m http.server 49218"]),("sudo",["sudo","/opt/root_start_and_wait_retry9_systemd.sh"])]
for comm,argv in yes: assert c.database_or_benchmark_process_identity(comm,argv),(comm,argv)
for comm,argv in no: assert not c.database_or_benchmark_process_identity(comm,argv),(comm,argv)
assert 'row["comm"] + " " + row["_command"]' not in P.read_text()
print("PASS_RETRY15_EXACT_PROCESS_IDENTITY")
'''.encode()
    unsigned = common(old, "mysql_tpch_retry15_exact_process_identity_support_successor_bundle_v1",
                      "SEALED_EXACT_PROCESS_IDENTITY_SUPPORT_NOT_EXECUTED",
                      OLD_SUPPORT_ID, OLD_SUPPORT_SHA)
    unsigned.update(
        process_identity_basis="comm_or_discrete_argv_token_basename",
        shell_c_payload_matching_prohibited=True,
        docker_container_inventory_unchanged=True,
        failed_retry14_attempt_token=FAILED,
        failed_retry14_rerun_prohibited=True,
    )
    return h.publish(SUPPORT_OUT, unsigned, content, ".retry15-support-")


def build_materializer(support):
    old = h.verify(MAT_OLD, OLD_MAT_ID, OLD_MAT_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (MAT_OLD / rel).read_text()
    source = h.once(
        source,
        f'SUPPORT_ID="{OLD_SUPPORT_ID}";SUPPORT_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{OLD_CORE_SHA}"',
        f'SUPPORT_ID="{support.name}";SUPPORT_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{core_sha}"',
        "materializer support closure",
    )
    content = h.copied(MAT_OLD, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry15_exact_process_identity_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry15_exact_process_identity_materializer_successor_bundle_v1",
                      "SEALED_RETRY15_MATERIALIZER_NOT_EXECUTED", OLD_MAT_ID, OLD_MAT_SHA)
    unsigned.update(support_bundle_id=support.name, support_manifest_sha256=support_sha,
                    support_entry_sha256=entry_sha, support_core_sha256=core_sha)
    return h.publish(MAT_OUT, unsigned, content, ".retry15-mat-")


def build_wrapper(support, materializer):
    old = h.verify(WRAP_OLD, OLD_WRAP_ID, OLD_WRAP_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (WRAP_OLD / rel).read_text()
    source = h.once(source, f'RETRY_ID="{OLD_MAT_ID}";RETRY_SHA="{OLD_MAT_SHA}"',
                    f'RETRY_ID="{materializer.name}";RETRY_SHA="{materializer_sha}"',
                    "wrapper materializer closure")
    source = h.once(
        source,
        f'COLLECTOR_ID="{OLD_SUPPORT_ID}";COLLECTOR_SHA="{OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{OLD_CORE_SHA}"',
        f'COLLECTOR_ID="{support.name}";COLLECTOR_SHA="{support_sha}";ENTRY_SHA="{entry_sha}";CORE_SHA="{core_sha}"',
        "wrapper support closure",
    )
    content = h.copied(WRAP_OLD, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content["builder/build_mysql_tpch_retry15_exact_process_identity_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry15_exact_process_identity_wrapper_successor_bundle_v1",
                      "SEALED_RETRY15_WRAPPER_NOT_EXECUTED", OLD_WRAP_ID, OLD_WRAP_SHA)
    unsigned.update(
        retry5_materializer_bundle_id=materializer.name,
        retry5_materializer_manifest_sha256=materializer_sha,
        collector_bundle_id=support.name,
        collector_manifest_sha256=support_sha,
        collector_entry_sha256=entry_sha,
        collector_core_sha256=core_sha,
    )
    return h.publish(WRAP_OUT, unsigned, content, ".retry15-wrap-")


def build_outer(support, materializer, wrapper):
    old = h.verify(BASE, OLD_OUTER_ID, OLD_OUTER_SHA)
    support_sha = h.sf(support / "bundle_manifest.json")
    entry_sha = h.sf(support / ENTRY_REL)
    core_sha = h.sf(support / CORE_REL)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    wrapper_sha = h.sf(wrapper / "bundle_manifest.json")
    source = (BASE / CONTROLLER).read_text()
    replacements = (
        (f'SUPPORT_ID = "{OLD_SUPPORT_ID}"', f'SUPPORT_ID = "{support.name}"', "support id"),
        (f'SUPPORT_SHA = "{OLD_SUPPORT_SHA}"', f'SUPPORT_SHA = "{support_sha}"', "support sha"),
        (f'MATERIALIZER_ID = "{OLD_MAT_ID}"', f'MATERIALIZER_ID = "{materializer.name}"', "materializer id"),
        (f'MATERIALIZER_SHA = "{OLD_MAT_SHA}"', f'MATERIALIZER_SHA = "{materializer_sha}"', "materializer sha"),
        (f'WRAPPER_ID = "{OLD_WRAP_ID}"', f'WRAPPER_ID = "{wrapper.name}"', "wrapper id"),
        (f'WRAPPER_SHA = "{OLD_WRAP_SHA}"', f'WRAPPER_SHA = "{wrapper_sha}"', "wrapper sha"),
    )
    for before, after, label in replacements:
        source = h.once(source, before, after, "outer " + label)
    source = h.once(source,
        f'FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT = "tpch-v2-20260803t214500p08-b235retry13solo7c8f1"\nROOT_SYSTEMD_ATTEMPT = "{FAILED}"',
        f'FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT = "tpch-v2-20260803t214500p08-b235retry13solo7c8f1"\nFAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT = "{FAILED}"\nROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"',
        "Retry15 attempt")
    source = h.once(source, f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED}.service"',
                    f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "Retry15 unit")
    source = h.once(source, f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry14_root_oneclick_" + FAILED)}")',
                    f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "Retry15 output")
    source = h.once(source,
        '        "failure_retry13_python_b_argv": root / "evidence/mysql_tpch_retry13_python_b_argv_identity_failure_v1.json",\n',
        '        "failure_retry13_python_b_argv": root / "evidence/mysql_tpch_retry13_python_b_argv_identity_failure_v1.json",\n'
        f'        "failure_retry14_process_identity": root / "{FAILURE}",\n',
        "Retry14 failure path")
    source = h.once(source,
        '        raise OneClickError("Retry13 python-B argv exact zero-credit evidence differs")\n    return paths\n',
        '''        raise OneClickError("Retry13 python-B argv exact zero-credit evidence differs")
    retry14 = sealed(paths["failure_retry14_process_identity"], "Retry14 process identity failure")
    if (retry14.get("schema_version") != "mysql_tpch_retry14_process_identity_false_positive_failure_v1" or retry14.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN" or retry14.get("attempt_token") != FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT or retry14.get("fresh_error") != "retry5 launch failed rc=2 stderr=NOT_READY: workload metric sample missing/stale/timed out: workload metric contiguous suffix is absent" or retry14.get("process_inventory_conflicts") != ["co_resident_cpu_or_device"] or retry14.get("current_pids") != {"bootstrap": None, "controller": None, "workload": None} or retry14.get("post_reconcile_passed") is not True or retry14.get("post_closure_passed") is not True or retry14.get("rerun_prohibited") is not True or retry14.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry14 process identity exact zero-credit evidence differs")
    return paths
''', "Retry14 failure verify")
    source = h.once(source,
        '    if attempt == FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT:\n        raise OneClickError("failed Retry13 python-B argv attempt rerun is prohibited")\n',
        '    if attempt == FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT:\n        raise OneClickError("failed Retry13 python-B argv attempt rerun is prohibited")\n'
        '    if attempt == FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT:\n        raise OneClickError("failed Retry14 process-identity attempt rerun is prohibited")\n',
        "Retry14 rerun guard")
    source = h.once(source,
        '        "failed_retry13_python_b_argv_evidence": str(paths["failure_retry13_python_b_argv"]),\n',
        '        "failed_retry13_python_b_argv_evidence": str(paths["failure_retry13_python_b_argv"]),\n'
        '        "failed_retry14_process_identity_attempt": FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT,\n'
        '        "failed_retry14_process_identity_evidence": str(paths["failure_retry14_process_identity"]),\n',
        "Retry14 plan evidence")
    source = h.once(source,
        '            or plan.get("attempt_token") == FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT\n            or plan.get("root_systemd_principal_required") is not True\n',
        '            or plan.get("attempt_token") == FAILED_RETRY13_PYTHON_B_ARGV_ATTEMPT\n'
        '            or plan.get("failed_retry14_process_identity_attempt") != FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT\n'
        '            or plan.get("attempt_token") == FAILED_RETRY14_PROCESS_IDENTITY_ATTEMPT\n'
        '            or plan.get("root_systemd_principal_required") is not True\n',
        "Retry14 execute guard")
    skip = ("builder/", "tests/", f"dependencies/support/{OLD_SUPPORT_ID}/",
            f"dependencies/materializer/{OLD_MAT_ID}/", f"dependencies/wrapper/{OLD_WRAP_ID}/")
    content = h.copied(BASE, old, skip)
    content[CONTROLLER] = source.encode()
    for dependency, prefix in ((support, "dependencies/support/"),
                               (materializer, "dependencies/materializer/"),
                               (wrapper, "dependencies/wrapper/")):
        for path in sorted(dependency.rglob("*")):
            if path.is_file():
                content[prefix + dependency.name + "/" + path.relative_to(dependency).as_posix()] = path.read_bytes()
    failure = {
        "schema_version": "mysql_tpch_retry14_process_identity_false_positive_failure_v1",
        "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "predecessor_bundle_id": OLD_OUTER_ID,
        "predecessor_manifest_sha256": OLD_OUTER_SHA,
        "attempt_token": FAILED,
        "unit": "ra-mysql-tpch-oneclick-root-" + FAILED + ".service",
        "historical_pids": {"root_controller": 18763, "bootstrap": 18810, "controller": 33636, "workload": 33637},
        "current_pids": {"bootstrap": None, "controller": None, "workload": None},
        "host_metric_samples": 101,
        "native_metric_samples": 101,
        "host_metric_last_seq": 100,
        "native_metric_last_seq": 100,
        "process_inventory_conflicts": ["co_resident_cpu_or_device"],
        "process_inventory_complete": True,
        "permission_failure_count": 0,
        "foreign_writer_count": 0,
        "fresh_error": "retry5 launch failed rc=2 stderr=NOT_READY: workload metric sample missing/stale/timed out: workload metric contiguous suffix is absent",
        "root_cause": "process relevance matched arbitrary substrings in shell -c payloads and launch paths instead of comm or discrete argv token basename",
        "post_reconcile_passed": True,
        "post_closure_passed": True,
        "rerun_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    raw = h.canonical(failure)
    content[FAILURE] = raw
    content[FAILURE + ".sha256"] = (h.sb(raw) + "  " + Path(FAILURE).name + "\n").encode()
    for rel in ("launch/root_start_and_wait_retry9_systemd.sh", "launch/run_retry9_root_systemd_principal.sh"):
        content[rel] = content[rel].decode().replace(FAILED, ATTEMPT).replace(
            "retry14_root_oneclick_" + ATTEMPT, "retry15_root_oneclick_" + ATTEMPT).encode()
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(schema_version="mysql_tpch_retry15_exact_process_identity_node04_command_v1",
                    attempt_token=ATTEMPT, systemd_unit=UNIT,
                    support_closure_bundle_id=support.name,
                    failed_retry14_attempt_rerun_prohibited=True)
    content[template_rel] = h.canonical(template)
    content["tests/test_mysql_tpch_retry15_exact_process_identity_successor_v1.py"] = f'''#!/usr/bin/env python3
import importlib.util,pathlib,subprocess,sys
B=pathlib.Path(sys.argv[1]).resolve();S=B/"dependencies/support/{support.name}";M=B/"dependencies/materializer/{materializer.name}";W=B/"dependencies/wrapper/{wrapper.name}";C=B/"{CONTROLLER}"
subprocess.run([sys.executable,"-B",str(S/"tests/test_mysql_tpch_retry15_exact_process_identity_v1.py"),str(S)],check=True)
q=importlib.util.spec_from_file_location("outer",C);o=importlib.util.module_from_spec(q);q.loader.exec_module(o);p=o.verify_package();assert p["support"]==S and p["materializer"]==M and p["wrapper"]==W;assert o.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}"
print("PASS_RETRY15_EXACT_PROCESS_IDENTITY_SUCCESSOR")
'''.encode()
    content["builder/build_mysql_tpch_retry15_exact_process_identity_successor_v1.py"] = Path(__file__).read_bytes()
    unsigned = common(old, "mysql_tpch_retry15_exact_process_identity_successor_bundle_v1",
                      "SEALED_LOCAL_RETRY15_EXACT_PROCESS_IDENTITY_NOT_EXECUTED",
                      OLD_OUTER_ID, OLD_OUTER_SHA)
    unsigned.update(
        support_bundle_id=support.name,
        support_manifest_sha256=support_sha,
        support_entry_sha256=entry_sha,
        support_core_sha256=core_sha,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=materializer_sha,
        wrapper_bundle_id=wrapper.name,
        wrapper_manifest_sha256=wrapper_sha,
        failed_retry14_attempt_token=FAILED,
        failed_retry14_rerun_prohibited=True,
        new_attempt_token=ATTEMPT,
        root_systemd_unit=UNIT,
        process_identity_exact=True,
    )
    return h.publish(OUT, unsigned, content, ".retry15-outer-")


def main():
    support = build_support()
    materializer = build_materializer(support)
    wrapper = build_wrapper(support, materializer)
    outer = build_outer(support, materializer, wrapper)
    print(json.dumps({
        "bundle": str(outer),
        "bundle_id": outer.name,
        "manifest_sha256": h.sf(outer / "bundle_manifest.json"),
        "support_bundle_id": support.name,
        "support_manifest_sha256": h.sf(support / "bundle_manifest.json"),
        "support_entry_sha256": h.sf(support / ENTRY_REL),
        "support_core_sha256": h.sf(support / CORE_REL),
        "materializer_bundle_id": materializer.name,
        "materializer_manifest_sha256": h.sf(materializer / "bundle_manifest.json"),
        "wrapper_bundle_id": wrapper.name,
        "wrapper_manifest_sha256": h.sf(wrapper / "bundle_manifest.json"),
        "attempt_token": ATTEMPT,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
