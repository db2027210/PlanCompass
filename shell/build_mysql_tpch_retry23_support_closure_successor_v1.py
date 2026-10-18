#!/usr/bin/env python3
"""Build Retry23 with one exact support identity through materializer and wrapper."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "retry22", ROOT / "shell/build_mysql_tpch_retry22_current_target_support_successor_v1.py"
)
r22 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r22)
h = r22.h

OLD_OUTER_ID = "eba1bf17a5269fcf85584c706d795df33e3a97879880ab1af573f562fd2d0cac"
OLD_OUTER_SHA = "5f74631418c7131756675ce0113affa76a46d381a5cc3dd4b0ab0025ac690abb"
OLD_OUTER = ROOT / "artifacts/mysql_tpch_retry22_current_target_support_control_successor_v1" / OLD_OUTER_ID
SUPPORT_ID = "942b6c0ad35366233a80c09df1aa70068dc40bf89926b7d615ea20584efe16f5"
SUPPORT_SHA = "25a469d5322fb996bdc3fcc91c20d3664ff1faecb7133c9871c95e6abfab2896"
OLD_MATERIALIZER_ID = "53b6e24e0f75a3f1d839d6e2f861aea5fec327d4b0956c6b702e48fb2c4a08b5"
OLD_MATERIALIZER_SHA = "7645c362f6df3d36816c821645cc0bb4cead70335604043e367108575706d244"
OLD_WRAPPER_ID = "680d76a285c07390c40f91de4c368a37609314cce3cbbb6299bbf321680ff522"
OLD_WRAPPER_SHA = "f2ac2a27a6db70ebfd9d25fc73b6a23913174024ed96f70bc3d1ceee8a425815"
SUPPORT = OLD_OUTER / "dependencies/support" / SUPPORT_ID
OLD_MATERIALIZER = OLD_OUTER / "dependencies/materializer" / OLD_MATERIALIZER_ID
OLD_WRAPPER = OLD_OUTER / "dependencies/wrapper" / OLD_WRAPPER_ID
ENTRY_REL = "qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py"
CORE_REL = "qualification/mysql_distributed_live_readback_qualification_v1.py"
OLD_ENTRY_SHA = "5f1e27816dd725ec40c1c4711c5a67423db64362389a4f67e2d9a29fdc16be5a"
ENTRY_SHA = "622e841a38253657f5aeb063eef6ea9615d56109f43848d6b967bdcdc3fad02e"
CORE_SHA = "76f799ae9bf0f9b4e6cb1f1a07852b00044d189a2bb8c52e0b945ee5af71f145"
FAILED_ATTEMPT = "tpch-v2-20260804t163000p08-b235retry22supportseal"
ATTEMPT = "tpch-v2-20260804t170000p08-b235retry23closure"
UNIT = "ra-mysql-tpch-oneclick-root-" + ATTEMPT + ".service"
RUNTIME = Path("/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729")
OUTPUT = RUNTIME / ("retry23_root_oneclick_" + ATTEMPT)
CONTROLLER_REL = "controller/mysql_tpch_retry9_one_click_v1.py"
BUILDER_REL = "builder/build_mysql_tpch_retry23_support_closure_successor_v1.py"
FAILURE_REL = "evidence/mysql_tpch_retry22_support_closure_failure_v1.json"
MATERIALIZER_OUT = ROOT / "artifacts/mysql_tpch_retry23_support_closure_materializer_successor_v1"
WRAPPER_OUT = ROOT / "artifacts/mysql_tpch_retry23_support_closure_wrapper_successor_v1"
OUTER_OUT = ROOT / "artifacts/mysql_tpch_retry23_support_closure_control_successor_v1"


def common(old, schema, status, predecessor_id, predecessor_sha):
    value = {k: v for k, v in old.items() if k not in (
        "bundle_id", "files", "schema_version", "status",
        "predecessor_bundle_id", "predecessor_manifest_sha256",
    )}
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
        qualification_credit=0,
        r0_credit=0,
        remote_actions=0,
    )
    return value


def build_materializer():
    old = h.verify(OLD_MATERIALIZER, OLD_MATERIALIZER_ID, OLD_MATERIALIZER_SHA)
    rel = "materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py"
    source = (OLD_MATERIALIZER / rel).read_text()
    source = h.once(
        source,
        f'SUPPORT_ID="{r22.OLD_SUPPORT_ID}";SUPPORT_SHA="{r22.OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',
        f'SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',
        "Retry23 materializer support closure",
    )
    content = h.copied(OLD_MATERIALIZER, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry23_materializer_support_closure_v1.py"] = f'''#!/usr/bin/env python3
import pathlib
B=pathlib.Path(__file__).resolve().parents[1];s=(B/"{rel}").read_text()
assert 'SUPPORT_ID="{SUPPORT_ID}"' in s and 'SUPPORT_SHA="{SUPPORT_SHA}"' in s
assert 'ENTRY_SHA="{ENTRY_SHA}"' in s and "{r22.OLD_SUPPORT_ID}" not in s
print("PASS_RETRY23_MATERIALIZER_SUPPORT_CLOSURE")
'''.encode()
    unsigned = common(
        old, "mysql_tpch_retry23_support_closure_materializer_successor_bundle_v1",
        "SEALED_RETRY23_MATERIALIZER_OFFLINE_NOT_EXECUTED",
        OLD_MATERIALIZER_ID, OLD_MATERIALIZER_SHA,
    )
    unsigned.update(
        support_bundle_id=SUPPORT_ID,
        support_manifest_sha256=SUPPORT_SHA,
        support_entry_sha256=ENTRY_SHA,
        support_core_sha256=CORE_SHA,
        failed_retry22_attempt=FAILED_ATTEMPT,
        failed_retry22_rerun_prohibited=True,
        stale_support_identity_prohibited=True,
        resource_gates_relaxed=False,
    )
    return h.publish(MATERIALIZER_OUT, unsigned, content, ".retry23-materializer-")


def build_wrapper(materializer):
    old = h.verify(OLD_WRAPPER, OLD_WRAPPER_ID, OLD_WRAPPER_SHA)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    rel = "controller/mysql_tpch_retry5_systemd219_completion_v1.py"
    source = (OLD_WRAPPER / rel).read_text()
    source = h.once(
        source,
        f'RETRY_ID="{OLD_MATERIALIZER_ID}";RETRY_SHA="{OLD_MATERIALIZER_SHA}"',
        f'RETRY_ID="{materializer.name}";RETRY_SHA="{materializer_sha}"',
        "Retry23 wrapper materializer closure",
    )
    source = h.once(
        source,
        f'COLLECTOR_ID="{r22.OLD_SUPPORT_ID}";COLLECTOR_SHA="{r22.OLD_SUPPORT_SHA}";ENTRY_SHA="{OLD_ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',
        f'COLLECTOR_ID="{SUPPORT_ID}";COLLECTOR_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}"',
        "Retry23 wrapper support closure",
    )
    content = h.copied(OLD_WRAPPER, old, ("builder/", "tests/"))
    content[rel] = source.encode()
    content[BUILDER_REL] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry23_wrapper_support_closure_v1.py"] = f'''#!/usr/bin/env python3
import pathlib
B=pathlib.Path(__file__).resolve().parents[1];s=(B/"{rel}").read_text()
for x in ("{materializer.name}","{materializer_sha}","{SUPPORT_ID}","{SUPPORT_SHA}","{ENTRY_SHA}"): assert x in s
for x in ("{OLD_MATERIALIZER_ID}","{r22.OLD_SUPPORT_ID}","{OLD_ENTRY_SHA}"): assert x not in s
print("PASS_RETRY23_WRAPPER_SUPPORT_CLOSURE")
'''.encode()
    unsigned = common(
        old, "mysql_tpch_retry23_support_closure_wrapper_successor_bundle_v1",
        "SEALED_RETRY23_WRAPPER_OFFLINE_NOT_EXECUTED",
        OLD_WRAPPER_ID, OLD_WRAPPER_SHA,
    )
    unsigned.update(
        retry5_materializer_bundle_id=materializer.name,
        retry5_materializer_manifest_sha256=materializer_sha,
        collector_bundle_id=SUPPORT_ID,
        collector_manifest_sha256=SUPPORT_SHA,
        collector_entry_sha256=ENTRY_SHA,
        collector_core_sha256=CORE_SHA,
        failed_retry22_attempt=FAILED_ATTEMPT,
        failed_retry22_rerun_prohibited=True,
        stale_support_identity_prohibited=True,
        resource_gates_relaxed=False,
    )
    return h.publish(WRAPPER_OUT, unsigned, content, ".retry23-wrapper-")


def failure_evidence():
    return {
        "schema_version": "mysql_tpch_retry22_support_closure_failure_v1",
        "status": "FAILED_ZERO_CREDIT_DO_NOT_RERUN",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "attempt_token": FAILED_ATTEMPT,
        "unit": "ra-mysql-tpch-oneclick-root-" + FAILED_ATTEMPT + ".service",
        "systemd_result": "exit-code",
        "systemd_exec_main_status": 2,
        "fresh_error": "Retry9 workload materializer failed rc=2 stderr=NOT_READY: support differs",
        "expected_by_stale_materializer": {
            "support_bundle_id": r22.OLD_SUPPORT_ID,
            "support_manifest_sha256": r22.OLD_SUPPORT_SHA,
            "support_entry_sha256": OLD_ENTRY_SHA,
        },
        "actual_outer_argument": {
            "support_bundle_id": SUPPORT_ID,
            "support_manifest_sha256": SUPPORT_SHA,
            "support_entry_sha256": ENTRY_SHA,
        },
        "stale_wrapper_would_also_reject_same_actual_support": True,
        "bootstrap_passed": True,
        "workload_started": False,
        "current_pids": {"bootstrap": None, "controller": None, "workload": None},
        "post_failure_reconcile_passed": True,
        "post_failure_closure_passed": True,
        "rerun_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "r0_credit": 0,
    }


def build_outer(materializer, wrapper):
    old = h.verify(OLD_OUTER, OLD_OUTER_ID, OLD_OUTER_SHA)
    materializer_sha = h.sf(materializer / "bundle_manifest.json")
    wrapper_sha = h.sf(wrapper / "bundle_manifest.json")
    source = (OLD_OUTER / CONTROLLER_REL).read_text()
    for before, after, label in (
        (f'MATERIALIZER_ID = "{OLD_MATERIALIZER_ID}"', f'MATERIALIZER_ID = "{materializer.name}"', "materializer id"),
        (f'MATERIALIZER_SHA = "{OLD_MATERIALIZER_SHA}"', f'MATERIALIZER_SHA = "{materializer_sha}"', "materializer sha"),
        (f'WRAPPER_ID = "{OLD_WRAPPER_ID}"', f'WRAPPER_ID = "{wrapper.name}"', "wrapper id"),
        (f'WRAPPER_SHA = "{OLD_WRAPPER_SHA}"', f'WRAPPER_SHA = "{wrapper_sha}"', "wrapper sha"),
        (f'ROOT_SYSTEMD_ATTEMPT = "{FAILED_ATTEMPT}"', f'ROOT_SYSTEMD_ATTEMPT = "{ATTEMPT}"', "attempt"),
        (f'ROOT_SYSTEMD_UNIT = "ra-mysql-tpch-oneclick-root-{FAILED_ATTEMPT}.service"', f'ROOT_SYSTEMD_UNIT = "{UNIT}"', "unit"),
        (f'ROOT_SYSTEMD_OUTPUT = Path("{RUNTIME / ("retry22_root_oneclick_" + FAILED_ATTEMPT)}")', f'ROOT_SYSTEMD_OUTPUT = Path("{OUTPUT}")', "output"),
    ):
        source = h.once(source, before, after, "Retry23 outer " + label)
    source = h.once(
        source,
        f'FAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT = "{r22.OLD_ATTEMPT}"',
        f'FAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT = "{r22.OLD_ATTEMPT}"\nFAILED_RETRY22_SUPPORT_CLOSURE_ATTEMPT = "{FAILED_ATTEMPT}"',
        "Retry23 failed attempt constant",
    )
    source = h.once(
        source,
        '        "failure_retry15_native_stall": root / "evidence/mysql_tpch_retry15_native_metric_stall_failure_v1.json",\n',
        '        "failure_retry15_native_stall": root / "evidence/mysql_tpch_retry15_native_metric_stall_failure_v1.json",\n'
        f'        "failure_retry22_support_closure": root / "{FAILURE_REL}",\n',
        "Retry23 failure path",
    )
    source = h.once(
        source,
        '    return paths\n\n\ndef run_checked(',
        f'''    retry22 = sealed(paths["failure_retry22_support_closure"], "Retry22 support closure failure")
    if (retry22.get("schema_version") != "mysql_tpch_retry22_support_closure_failure_v1"
            or retry22.get("status") != "FAILED_ZERO_CREDIT_DO_NOT_RERUN"
            or retry22.get("attempt_token") != FAILED_RETRY22_SUPPORT_CLOSURE_ATTEMPT
            or retry22.get("fresh_error") != "Retry9 workload materializer failed rc=2 stderr=NOT_READY: support differs"
            or retry22.get("workload_started") is not False
            or retry22.get("current_pids") != {{"bootstrap": None, "controller": None, "workload": None}}
            or retry22.get("post_failure_reconcile_passed") is not True
            or retry22.get("post_failure_closure_passed") is not True
            or retry22.get("rerun_prohibited") is not True
            or retry22.get("accepted_rounds_credit") != 0):
        raise OneClickError("Retry22 support closure exact zero-credit evidence differs")
    return paths


def run_checked(''',
        "Retry23 exact failure verification",
    )
    source = h.once(
        source,
        '    if attempt == FAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT:\n        raise OneClickError("failed Retry21 current-target-support attempt rerun is prohibited")\n',
        '    if attempt == FAILED_RETRY21_CURRENT_TARGET_SUPPORT_ATTEMPT:\n        raise OneClickError("failed Retry21 current-target-support attempt rerun is prohibited")\n'
        '    if attempt == FAILED_RETRY22_SUPPORT_CLOSURE_ATTEMPT:\n        raise OneClickError("failed Retry22 support-closure attempt rerun is prohibited")\n',
        "Retry23 rerun guard",
    )
    skip = (
        "builder/", "tests/",
        "dependencies/materializer/" + OLD_MATERIALIZER_ID + "/",
        "dependencies/wrapper/" + OLD_WRAPPER_ID + "/",
    )
    content = h.copied(OLD_OUTER, old, skip)
    content[CONTROLLER_REL] = source.encode()
    for dependency, prefix in ((materializer, "dependencies/materializer/"), (wrapper, "dependencies/wrapper/")):
        for path in sorted(dependency.rglob("*")):
            if path.is_file():
                content[prefix + dependency.name + "/" + path.relative_to(dependency).as_posix()] = path.read_bytes()
    raw_failure = h.canonical(failure_evidence())
    content[FAILURE_REL] = raw_failure
    content[FAILURE_REL + ".sha256"] = (h.sb(raw_failure) + "  " + Path(FAILURE_REL).name + "\n").encode()
    for rel in ("launch/root_start_and_wait_retry9_systemd.sh", "launch/run_retry9_root_systemd_principal.sh"):
        content[rel] = content[rel].decode().replace(FAILED_ATTEMPT, ATTEMPT).replace(
            "retry22_root_oneclick_" + ATTEMPT, "retry23_root_oneclick_" + ATTEMPT
        ).encode()
    template_rel = "templates/node04_one_click_command.json"
    template = json.loads(content[template_rel])
    template.update(
        schema_version="mysql_tpch_retry23_support_closure_node04_command_v1",
        attempt_token=ATTEMPT,
        systemd_unit=UNIT,
        support_bundle_id=SUPPORT_ID,
        support_closure_bundle_id=SUPPORT_ID,
        support_collector_bundle_id=SUPPORT_ID,
        support_collector_manifest_sha256=SUPPORT_SHA,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=materializer_sha,
        wrapper_bundle_id=wrapper.name,
        wrapper_manifest_sha256=wrapper_sha,
        failed_retry22_attempt_rerun_prohibited=True,
        bootstrap_spec_valid_minutes=60,
        workload_authority_valid_seconds=1800,
    )
    content[template_rel] = h.canonical(template)
    content[BUILDER_REL] = Path(__file__).read_bytes()
    content["tests/test_mysql_tpch_retry23_support_closure_control_v1.py"] = f'''#!/usr/bin/env python3
import importlib.util,json,pathlib,sys
B=pathlib.Path(sys.argv[1]).resolve();C=B/"{CONTROLLER_REL}"
q=importlib.util.spec_from_file_location("retry23",C);m=importlib.util.module_from_spec(q);q.loader.exec_module(m);p=m.verify_package()
assert p["support"].name=="{SUPPORT_ID}" and p["materializer"].name=="{materializer.name}" and p["wrapper"].name=="{wrapper.name}"
assert m.ROOT_SYSTEMD_ATTEMPT=="{ATTEMPT}" and m.FAILED_RETRY22_SUPPORT_CLOSURE_ATTEMPT=="{FAILED_ATTEMPT}"
s=C.read_text();assert "failed Retry22 support-closure attempt rerun is prohibited" in s and '"--valid-minutes", "60"' in s and '"--valid-seconds", "1800"' in s
t=json.loads((B/"{template_rel}").read_text());assert t["bootstrap_spec_valid_minutes"]==60 and t["workload_authority_valid_seconds"]==1800
assert t["support_bundle_id"]==t["support_closure_bundle_id"]==t["support_collector_bundle_id"]=="{SUPPORT_ID}"
f=json.loads((B/"{FAILURE_REL}").read_text());assert f["accepted_rounds_credit"]==0 and f["rerun_prohibited"] is True and f["workload_started"] is False
print("PASS_RETRY23_SUPPORT_CLOSURE_CONTROL")
'''.encode()
    unsigned = common(
        old, "mysql_tpch_retry23_support_closure_control_successor_bundle_v1",
        "SEALED_LOCAL_RETRY23_NOT_EXECUTED", OLD_OUTER_ID, OLD_OUTER_SHA,
    )
    unsigned.update(
        support_bundle_id=SUPPORT_ID,
        support_manifest_sha256=SUPPORT_SHA,
        support_entry_sha256=ENTRY_SHA,
        support_core_sha256=CORE_SHA,
        materializer_bundle_id=materializer.name,
        materializer_manifest_sha256=materializer_sha,
        wrapper_bundle_id=wrapper.name,
        wrapper_manifest_sha256=wrapper_sha,
        failed_retry22_attempt=FAILED_ATTEMPT,
        failed_retry22_rerun_prohibited=True,
        failed_retry22_accepted_rounds_credit=0,
        new_attempt_token=ATTEMPT,
        root_systemd_unit=UNIT,
        bootstrap_spec_valid_minutes=60,
        workload_authority_valid_seconds=1800,
        exact_support_identity_all_layers=True,
        stale_support_identity_prohibited=True,
        minimum_samples_unchanged=181,
        sampling_hz_unchanged=1,
        total_qualification_upper_bound_seconds=600,
        resource_gates_relaxed=False,
        synthetic_or_repeat_samples_prohibited=True,
    )
    return h.publish(OUTER_OUT, unsigned, content, ".retry23-outer-")


def main():
    h.verify(SUPPORT, SUPPORT_ID, SUPPORT_SHA)
    if h.sf(SUPPORT / ENTRY_REL) != ENTRY_SHA or h.sf(SUPPORT / CORE_REL) != CORE_SHA:
        raise RuntimeError("Retry23 support file identity differs")
    materializer = build_materializer()
    wrapper = build_wrapper(materializer)
    outer = build_outer(materializer, wrapper)
    print(json.dumps({
        "bundle": str(outer),
        "bundle_id": outer.name,
        "manifest_sha256": h.sf(outer / "bundle_manifest.json"),
        "support_bundle_id": SUPPORT_ID,
        "support_manifest_sha256": SUPPORT_SHA,
        "materializer_bundle_id": materializer.name,
        "materializer_manifest_sha256": h.sf(materializer / "bundle_manifest.json"),
        "wrapper_bundle_id": wrapper.name,
        "wrapper_manifest_sha256": h.sf(wrapper / "bundle_manifest.json"),
        "attempt_token": ATTEMPT,
        "systemd_unit": UNIT,
        "bootstrap_spec_valid_minutes": 60,
        "workload_authority_valid_seconds": 1800,
    }, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise SystemExit("NOT_READY: " + str(exc))
